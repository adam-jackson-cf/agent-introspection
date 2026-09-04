from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from experiments.dashboard_prototype.contracts import (
    EvidenceProvenance,
    ExperimentResult,
    PrototypeContractError,
)
from experiments.dashboard_prototype.pipeline_ledger import (
    LEDGER_REMOTE_QUERY_ID,
    LEDGER_REMOTE_SQL,
    FreePageState,
    LedgerCheckResult,
    LedgerCheckType,
    LedgerObservation,
    LedgerReduction,
    LedgerReductionRequest,
    LedgerRemotePopulationAuthority,
    MigrationState,
    build_ledger_proof,
    build_ledger_remote_query,
    parse_ledger_remote_result,
    reconcile_ledger_remote,
    reduce_ledger_observations,
)

START = datetime(2026, 1, 1, tzinfo=UTC)
END = START + timedelta(hours=10)
EVALUATED_AT = END + timedelta(hours=2)


DEFAULT_OBSERVATION = LedgerObservation(
    database_identity="database-a",
    check_type=LedgerCheckType.INTEGRITY,
    completed_at=END - timedelta(hours=1),
    result=LedgerCheckResult.PASSED,
    backup_completed_at=END - timedelta(hours=2),
    database_bytes=400,
    wal_bytes=20,
    freelist_count=10,
    page_count=100,
    migration_state=MigrationState.CURRENT,
    runtime_host="db-host-1",
)


def observation(database: str = "database-a") -> LedgerObservation:
    return replace(DEFAULT_OBSERVATION, database_identity=database)


def reduce(*observations: LedgerObservation) -> LedgerReduction:
    return reduce_ledger_observations(
        LedgerReductionRequest(
            observations=observations,
            start=START,
            end=END,
            evaluated_at=EVALUATED_AT,
            stale_check_after=timedelta(hours=2),
            stale_backup_after=timedelta(hours=3),
        )
    )


def test_selects_latest_completed_observation_per_database_and_check_type() -> None:
    older = replace(observation(), completed_at=END - timedelta(hours=4), freelist_count=2)
    latest = replace(observation(), completed_at=END - timedelta(hours=1), freelist_count=20)

    reduction = reduce(older, latest)

    assert reduction.counts.selected_databases == 1
    assert reduction.counts.selected_checks == 1
    assert reduction.databases[0].completed_at == latest.completed_at
    assert reduction.databases[0].free_page_percentage.percent == 20


def test_preserves_latest_integrity_and_maintenance_rows_for_one_database() -> None:
    failed_integrity = replace(
        observation(),
        completed_at=END - timedelta(hours=2),
        result=LedgerCheckResult.FAILED,
    )
    passing_maintenance = replace(
        observation(),
        check_type=LedgerCheckType.MAINTENANCE,
        completed_at=END - timedelta(hours=1),
    )

    reduction = reduce(failed_integrity, passing_maintenance)
    proof = build_ledger_proof(
        reduction,
        run_id="ledger-run-per-check-type",
        provenance=EvidenceProvenance.FRESH_REAL,
        source_boundary="bounded-ledger",
    )

    assert [(row.database_identity, row.check_type) for row in reduction.databases] == [
        ("database-a", LedgerCheckType.INTEGRITY),
        ("database-a", LedgerCheckType.MAINTENANCE),
    ]
    assert reduction.counts.selected_databases == 1
    assert reduction.counts.selected_checks == 2
    assert reduction.counts.failed_checks == 1
    assert proof.metrics["in_window_observations"] == 2
    assert proof.metrics["selected_checks"] == 2
    assert proof.evidence_ids == ("database-a:integrity", "database-a:maintenance")
    assert proof.result is ExperimentResult.FAILED


def test_uses_open_start_and_closed_end_boundaries() -> None:
    at_start = replace(observation("at-start"), completed_at=START)
    at_end = replace(observation("at-end"), completed_at=END)

    reduction = reduce(at_start, at_end)

    assert [database.database_identity for database in reduction.databases] == ["at-end"]


def test_calculates_exact_stale_age_boundaries() -> None:
    exact = replace(
        observation(),
        completed_at=EVALUATED_AT - timedelta(hours=3),
        backup_completed_at=EVALUATED_AT - timedelta(hours=4),
    )
    fresh = replace(
        observation("fresh"),
        completed_at=EVALUATED_AT - timedelta(hours=3) + timedelta(microseconds=1),
        backup_completed_at=EVALUATED_AT - timedelta(hours=4) + timedelta(microseconds=1),
    )

    reduction = reduce_ledger_observations(
        LedgerReductionRequest(
            observations=(exact, fresh),
            start=START,
            end=END,
            evaluated_at=EVALUATED_AT,
            stale_check_after=timedelta(hours=3),
            stale_backup_after=timedelta(hours=4),
        )
    )
    assert reduction.databases[0].stale_check is True
    assert reduction.databases[0].stale_backup is True
    assert reduction.databases[1].stale_check is False
    assert reduction.databases[1].stale_backup is False


@pytest.mark.parametrize("page_count", [0, -1])
def test_marks_nonpositive_page_count_as_unavailable(page_count: int) -> None:
    reduction = reduce(replace(observation(), page_count=page_count))

    percentage = reduction.databases[0].free_page_percentage
    assert percentage.state is FreePageState.UNAVAILABLE
    assert percentage.percent is None


def test_calculates_free_page_percentage_for_positive_page_count() -> None:
    reduction = reduce(replace(observation(), freelist_count=3, page_count=8))

    assert reduction.databases[0].free_page_percentage.percent == 37.5


def test_preserves_distinct_databases_and_migration_states() -> None:
    reduction = reduce(
        replace(observation("database-a"), migration_state=MigrationState.PENDING),
        replace(observation("database-b"), migration_state=MigrationState.FAILED),
    )

    assert [database.database_identity for database in reduction.databases] == [
        "database-a",
        "database-b",
    ]
    assert reduction.counts.pending_migrations == 1
    assert reduction.counts.failed_migrations == 1


def test_missing_required_fields_block_proof_with_exact_boundaries() -> None:
    reduction = reduce(replace(observation(), backup_completed_at=None, runtime_host=None))

    proof = build_ledger_proof(
        reduction,
        run_id="ledger-run-1",
        provenance=EvidenceProvenance.FRESH_REAL,
        source_boundary="maintenance_observations.completed_at > start AND <= end",
    )

    assert proof.result is ExperimentResult.BLOCKED
    assert proof.blocked_boundaries == (
        "observation[0].backup_completed_at",
        "observation[0].runtime_host",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("database_identity", "/private/db"),
        ("runtime_host", "host/payload"),
        ("runtime_host", '{"payload":"not-allowed"}'),
    ],
)
def test_rejects_unsafe_paths_and_payload_like_strings(field: str, value: str) -> None:
    if field == "database_identity":
        with pytest.raises(PrototypeContractError, match="unsafe"):
            replace(observation(), database_identity=value)
    else:
        with pytest.raises(PrototypeContractError, match="unsafe"):
            replace(observation(), runtime_host=value)


def test_failed_checks_or_migrations_fail_proof_and_retained_data_cannot_prove() -> None:
    failed = reduce(replace(observation(), result=LedgerCheckResult.FAILED))
    retained = reduce(observation())

    assert (
        build_ledger_proof(
            failed,
            run_id="ledger-run-2",
            provenance=EvidenceProvenance.FRESH_REAL,
            source_boundary="bounded-ledger",
        ).result
        is ExperimentResult.FAILED
    )
    assert (
        build_ledger_proof(
            retained,
            run_id="ledger-run-3",
            provenance=EvidenceProvenance.RETAINED,
            source_boundary="bounded-ledger",
        ).result
        is ExperimentResult.FAILED
    )


def test_reducer_is_deterministic_for_input_order() -> None:
    observations = (
        replace(observation("database-b"), completed_at=END - timedelta(hours=2)),
        replace(observation("database-a"), completed_at=END - timedelta(hours=3)),
        replace(observation("database-a"), completed_at=END - timedelta(hours=1)),
    )

    assert reduce(*observations) == reduce(*reversed(observations))


def remote_row(**overrides: object) -> dict[str, object]:
    completed_at = overrides.pop("completed_at", END - timedelta(hours=1))
    backup_completed_at = overrides.pop("backup_completed_at", END - timedelta(hours=2))
    assert isinstance(completed_at, datetime)
    assert isinstance(backup_completed_at, datetime)
    row: dict[str, object] = {
        "event_id": "ledger-event-1",
        "database_identity": "database-a",
        "check_type": "integrity",
        "completed_at_ns": int(completed_at.timestamp() * 1_000_000_000),
        "result": "passed",
        "backup_completed_at_ns": int(backup_completed_at.timestamp() * 1_000_000_000),
        "database_bytes": 400,
        "wal_bytes": 20,
        "freelist_count": 10,
        "page_count": 100,
        "migration_state": "current",
        "runtime_host": "db-host-1",
    }
    row.update(overrides)
    return row


def remote_authority(
    *event_ids: str,
) -> LedgerRemotePopulationAuthority:
    return LedgerRemotePopulationAuthority(event_ids, START, END)


def test_e6_remote_query_requires_exact_population_authority() -> None:
    assert (
        build_ledger_remote_query(
            LedgerRemotePopulationAuthority(None, START, END),
            start_bucket=1,
            end_bucket=2,
            event_name="dashboard_prototype.pipeline_snapshot.v1",
            run_id_hash="run-hash",
        )
        is None
    )

    query = build_ledger_remote_query(
        remote_authority("ledger-event-1", "ledger-event-2"),
        start_bucket=1,
        end_bucket=2,
        event_name="dashboard_prototype.pipeline_snapshot.v1",
        run_id_hash="run-hash",
    )

    assert query is not None
    assert query.parameters["query_id"] == LEDGER_REMOTE_QUERY_ID
    assert query.parameters["event_0"] == "ledger-event-1"
    assert query.parameters["event_1"] == "ledger-event-2"
    assert "{event_0:String}, {event_1:String}" in query.sql
    assert "timestamp > {maintenance_start_ns:UInt64}" in LEDGER_REMOTE_SQL
    assert "timestamp <= {maintenance_end_ns:UInt64}" in LEDGER_REMOTE_SQL
    assert "attributes_string['event.id'] IN ({event_ids})" in LEDGER_REMOTE_SQL
    assert "isFinite(attributes_number['dashboard.database_bytes'])" in LEDGER_REMOTE_SQL
    assert "= floor(attributes_number['dashboard.database_bytes'])" in LEDGER_REMOTE_SQL
    assert "toUInt64(attributes_number['dashboard.database_bytes'])" in LEDGER_REMOTE_SQL


def test_e6_remote_parser_preserves_allowlisted_scalar_types() -> None:
    parsed = parse_ledger_remote_result([remote_row()])

    assert parsed[0].event_id == "ledger-event-1"
    assert parsed[0].completed_at == END - timedelta(hours=1)
    assert parsed[0].backup_completed_at == END - timedelta(hours=2)
    assert type(parsed[0].database_bytes) is int
    assert type(parsed[0].wal_bytes) is int
    assert parsed[0].check_type is LedgerCheckType.INTEGRITY
    assert parsed[0].result is LedgerCheckResult.PASSED
    assert parsed[0].migration_state is MigrationState.CURRENT


def test_e6_parser_deduplicates_identical_immutable_event_rows() -> None:
    duplicate = remote_row()

    parsed = parse_ledger_remote_result([duplicate, duplicate.copy()])
    result = reconcile_ledger_remote(remote_authority("ledger-event-1"), parsed)

    assert len(parsed) == 1
    assert result.blocked_inputs == ()
    assert result.selected == parsed


def test_e6_parser_rejects_conflicting_immutable_event_payloads() -> None:
    with pytest.raises(PrototypeContractError, match="conflicting payloads"):
        parse_ledger_remote_result(
            [
                remote_row(),
                remote_row(database_bytes=401),
            ]
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda row: row.update({"unexpected_payload": "not-allowlisted"}),
        lambda row: row.pop("runtime_host"),
        lambda row: row.update({"database_bytes": 1.5}),
        lambda row: row.update({"completed_at_ns": True}),
        lambda row: row.update({"completed_at_ns": 1}),
        lambda row: row.update({"check_type": "quick_check"}),
        lambda row: row.update({"runtime_host": "/private/db"}),
    ],
)
def test_e6_remote_parser_rejects_non_allowlisted_or_imprecise_fields(
    mutate: Callable[[dict[str, object]], None],
) -> None:
    row = remote_row()
    mutate(row)

    with pytest.raises(PrototypeContractError):
        parse_ledger_remote_result([row])


def test_e6_oracle_selects_latest_row_from_exact_bounded_authority() -> None:
    older = remote_row(event_id="ledger-event-1", completed_at=END - timedelta(hours=2))
    latest = remote_row(event_id="ledger-event-2", completed_at=END - timedelta(hours=1))
    maintenance = remote_row(
        event_id="ledger-event-3",
        check_type="maintenance",
        completed_at=END - timedelta(hours=1),
    )

    result = reconcile_ledger_remote(
        remote_authority("ledger-event-1", "ledger-event-2", "ledger-event-3"),
        parse_ledger_remote_result([older, latest, maintenance]),
    )

    assert result.blocked_inputs == ()
    assert result.selected is not None
    assert [row.event_id for row in result.selected] == ["ledger-event-2", "ledger-event-3"]


def test_e6_oracle_blocks_missing_authority_without_zero_population() -> None:
    result = reconcile_ledger_remote(
        LedgerRemotePopulationAuthority(None, None, None),
        None,
    )

    assert result.selected is None
    assert result.blocked_inputs == (
        "immutable_event_ids",
        "maintenance_end",
        "maintenance_observations",
        "maintenance_start",
    )


def test_e6_oracle_blocks_incomplete_or_stale_authority_coverage() -> None:
    authorized = remote_authority("ledger-event-1")
    outside_id = parse_ledger_remote_result([remote_row(event_id="ledger-event-2")])
    outside_time = parse_ledger_remote_result(
        [remote_row(completed_at=START, event_id="ledger-event-1")]
    )
    empty = parse_ledger_remote_result([])
    subset = parse_ledger_remote_result([remote_row(event_id="ledger-event-1")])
    superset = parse_ledger_remote_result(
        [
            remote_row(event_id="ledger-event-1"),
            remote_row(event_id="ledger-event-2", check_type="maintenance"),
        ]
    )

    assert reconcile_ledger_remote(authorized, outside_id).blocked_inputs == (
        "immutable_event_id_coverage",
    )
    assert reconcile_ledger_remote(authorized, outside_time).blocked_inputs == (
        "maintenance_time_coverage",
    )
    assert reconcile_ledger_remote(authorized, empty).blocked_inputs == (
        "immutable_event_id_coverage",
    )
    assert reconcile_ledger_remote(
        remote_authority("ledger-event-1", "ledger-event-2"), subset
    ).blocked_inputs == ("immutable_event_id_coverage",)
    assert reconcile_ledger_remote(authorized, superset).blocked_inputs == (
        "immutable_event_id_coverage",
    )

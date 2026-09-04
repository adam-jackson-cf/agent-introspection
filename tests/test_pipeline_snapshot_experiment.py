from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from experiments.dashboard_prototype.contracts import EvidenceProvenance, ExperimentResult
from experiments.dashboard_prototype.pipeline_snapshot import (
    A01_LATEST_PIPELINE_SNAPSHOT_SQL,
    A02_SCAN_OUTCOMES_SQL,
    A05_SCAN_WORKLOAD_DURATION_SQL,
    CompletedScanSnapshotCandidate,
    DurableSnapshotPopulationOracle,
    PipelineSnapshotAuthorityRecord,
    PipelineSnapshotProofInput,
    ScanErrorClass,
    ScanTerminalClass,
    SnapshotPopulationCounts,
    build_pipeline_snapshot_proof,
    oracle_a01_latest_pipeline_snapshot,
    oracle_a02_scan_outcomes,
    oracle_a05_scan_workload_duration,
    parse_a01_latest_pipeline_snapshot_row,
    parse_a02_scan_outcome_row,
    parse_a05_scan_workload_duration_row,
    reduce_pipeline_snapshot,
)

START = datetime(2026, 9, 1, tzinfo=UTC)
END = START + timedelta(hours=1)


def _counts() -> SnapshotPopulationCounts:
    return SnapshotPopulationCounts(
        rows=10,
        logs=9,
        traces=8,
        context_events=7,
        canonical_activities=6,
        source_sessions=5,
        pending_outbox=4,
        failed_during_drain=3,
    )


def _snapshot() -> CompletedScanSnapshotCandidate:
    return CompletedScanSnapshotCandidate(
        scan_id="scan-1",
        source_time=END,
        payload_schema_version=1,
        terminal_class=ScanTerminalClass.COMPLETED,
        duration_ms=250,
        error_class=ScanErrorClass.NONE,
        counts=_counts(),
        bounded_drain_id="drain-1",
    )


def _oracle() -> DurableSnapshotPopulationOracle:
    return DurableSnapshotPopulationOracle(counts=_counts(), bounded_drain_id="drain-1")


def _proof(
    *,
    provenance: EvidenceProvenance = EvidenceProvenance.FRESH_REAL,
    snapshots: tuple[CompletedScanSnapshotCandidate, ...] | None = None,
    start: datetime = START,
    end: datetime = END,
    durable_oracle: DurableSnapshotPopulationOracle | None = None,
):
    proof_input = PipelineSnapshotProofInput(
        snapshots=(_snapshot(),) if snapshots is None else snapshots,
        start=start,
        end=end,
        durable_oracle=_oracle() if durable_oracle is None else durable_oracle,
    )
    return build_pipeline_snapshot_proof(
        run_id="run-1",
        provenance=provenance,
        source_boundary="source_time > start AND source_time <= end",
        proof_input=proof_input,
    )


def test_proves_exact_reconciliation_with_rows_only_workload_normalization() -> None:
    proof = _proof()

    assert proof.result is ExperimentResult.PROVEN
    assert proof.assertions["durable_population_reconciles"]
    assert proof.metrics["latest_workload_count"] == 10
    assert proof.metrics["latest_duration_ms"] == 250
    assert proof.metrics["latest_failed_during_drain_attempt_events"] == 3


def test_blocks_missing_required_boundaries_and_nonfresh_provenance() -> None:
    snapshot = replace(_snapshot(), payload_schema_version=None)
    oracle = replace(_oracle(), counts=replace(_counts(), rows=None))
    proof = _proof(
        provenance=EvidenceProvenance.SYNTHETIC,
        snapshots=(snapshot,),
        durable_oracle=oracle,
    )

    assert proof.result is ExperimentResult.BLOCKED
    assert proof.blocked_boundaries == (
        "durable_oracle.rows",
        "fresh_real_provenance",
        "snapshot.payload_schema_version",
    )


def test_fails_when_any_independent_durable_count_mismatches() -> None:
    proof = _proof(durable_oracle=replace(_oracle(), counts=replace(_counts(), traces=99)))

    assert proof.result is ExperimentResult.FAILED
    assert not proof.assertions["durable_population_reconciles"]


def test_failed_attempt_events_are_not_derived_from_pending_outbox() -> None:
    snapshot = replace(
        _snapshot(), counts=replace(_counts(), pending_outbox=0, failed_during_drain=3)
    )
    proof = _proof(
        snapshots=(snapshot,),
        durable_oracle=replace(_oracle(), counts=snapshot.counts),
    )

    assert proof.result is ExperimentResult.PROVEN
    assert proof.metrics["latest_pending_outbox"] == 0
    assert proof.metrics["latest_failed_during_drain_attempt_events"] == 3


def test_rejects_inconsistent_terminal_and_error_classes() -> None:
    proof = _proof(snapshots=(replace(_snapshot(), error_class=ScanErrorClass.SCAN_ERROR),))

    assert proof.result is ExperimentResult.FAILED
    assert not proof.assertions["snapshot_contract_valid"]


@pytest.mark.parametrize(
    ("source_time", "selected"),
    [(START, False), (START + timedelta(microseconds=1), True), (END, True)],
)
def test_uses_open_closed_source_time_range_endpoints(
    source_time: datetime, selected: bool
) -> None:
    snapshot = replace(_snapshot(), source_time=source_time)
    if not selected:
        with pytest.raises(ValueError, match="contains no completed scan"):
            reduce_pipeline_snapshot(
                (snapshot,),
                start=START,
                end=END,
                durable_oracle=_oracle(),
            )
        return

    reduction = reduce_pipeline_snapshot(
        (snapshot,),
        start=START,
        end=END,
        durable_oracle=_oracle(),
    )
    assert reduction.latest_snapshot is snapshot


def test_coalesces_identical_duplicates_and_selects_latest_deterministically() -> None:
    early = replace(
        _snapshot(),
        scan_id="scan-early",
        source_time=START + timedelta(minutes=1),
        duration_ms=1,
    )
    late_a = replace(_snapshot(), scan_id="scan-a", source_time=END, duration_ms=2)
    late_b = replace(late_a, scan_id="scan-b", duration_ms=3)
    duplicate_late_b = replace(late_b)

    first = reduce_pipeline_snapshot(
        (late_a, early, late_b, duplicate_late_b),
        start=START,
        end=END,
        durable_oracle=_oracle(),
    )
    second = reduce_pipeline_snapshot(
        (duplicate_late_b, late_b, late_a, early),
        start=START,
        end=END,
        durable_oracle=_oracle(),
    )
    proof = _proof(snapshots=(duplicate_late_b, late_b))

    assert first == second
    assert first.latest_snapshot.scan_id == "scan-b"
    assert first.latest_duration_ms == 3
    assert first.selected_snapshot_count == 3
    assert proof.metrics["selected_snapshot_count"] == 1


def test_rejects_conflicting_duplicates_for_one_scan_identity() -> None:
    snapshot = _snapshot()
    conflict = replace(snapshot, duration_ms=251)
    proof = _proof(snapshots=(snapshot, conflict))

    with pytest.raises(ValueError, match="conflicting snapshot candidates"):
        reduce_pipeline_snapshot(
            (snapshot, conflict),
            start=START,
            end=END,
            durable_oracle=_oracle(),
        )
    assert proof.result is ExperimentResult.FAILED
    assert not proof.assertions["snapshot_contract_valid"]


def _authority_record(
    *,
    event_id: str = "event-1",
    completed_at_ns: int = 1_000,
    terminal: tuple[ScanTerminalClass, ScanErrorClass] = (
        ScanTerminalClass.COMPLETED,
        ScanErrorClass.NONE,
    ),
    duration_ms: int = 250,
    rows: int = 10,
) -> PipelineSnapshotAuthorityRecord:
    return PipelineSnapshotAuthorityRecord(
        event_id=event_id,
        completed_at_ns=completed_at_ns,
        payload_schema_version=1,
        terminal_class=terminal[0],
        duration_ms=duration_ms,
        error_class=terminal[1],
        counts=replace(_counts(), rows=rows),
        bounded_drain_id="drain-1",
    )


def test_a01_parser_and_oracle_preserve_exact_snapshot_types() -> None:
    row = {
        "event_id": "event-2",
        "completed_at_ns": 2_000,
        "payload_schema_version": 1,
        "terminal_class": "completed",
        "duration_ms": 500,
        "error_class": "none",
        "rows": 20,
        "logs": 9,
        "traces": 8,
        "context_events": 7,
        "canonical_activities": 6,
        "source_sessions": 5,
        "pending_outbox": 4,
        "failed_during_drain": 3,
        "bounded_drain_id": "drain-2",
    }

    result = oracle_a01_latest_pipeline_snapshot(
        (_authority_record(completed_at_ns=1_000), parse_a01_latest_pipeline_snapshot_row(row))
    )

    assert result.blocked_boundaries == ()
    assert result.calculation is not None
    assert result.calculation.event_id == "event-2"
    assert isinstance(result.calculation.completed_at_ns, int)
    assert result.calculation.duration_ms == 500
    assert result.calculation.rows_per_second == 40.0
    assert (
        parse_a02_scan_outcome_row(
            {
                "terminal_class": "completed",
                "terminal_scan_count": 1,
                "terminal_scan_percent": 100.0,
            }
        ).terminal_scan_percent
        == 100.0
    )
    assert (
        parse_a05_scan_workload_duration_row(
            {
                "successful_scan_count": 1,
                "positive_duration_scan_count": 1,
                "duration_p50_ms": 500.0,
                "duration_p95_ms": 500.0,
                "rows_p50": 20.0,
                "rows_p95": 20.0,
                "rows_per_second_p50": 40.0,
                "rows_per_second_p95": 40.0,
            }
        ).rows_per_second_p95
        == 40.0
    )


def test_a02_and_a05_oracles_calculate_terminal_and_percentile_rows() -> None:
    records = (
        _authority_record(event_id="event-1", completed_at_ns=1, duration_ms=100, rows=10),
        _authority_record(event_id="event-2", completed_at_ns=2, duration_ms=200, rows=20),
        _authority_record(event_id="event-3", completed_at_ns=3, duration_ms=300, rows=30),
        _authority_record(
            event_id="event-4",
            completed_at_ns=4,
            terminal=(ScanTerminalClass.FAILED, ScanErrorClass.SCAN_ERROR),
            duration_ms=400,
            rows=40,
        ),
    )

    outcomes = oracle_a02_scan_outcomes(records)
    workload = oracle_a05_scan_workload_duration(records)

    assert outcomes.terminal_scan_count == 4
    assert [
        (row.terminal_class, row.terminal_scan_count, row.terminal_scan_percent)
        for row in outcomes.outcomes
    ] == [
        (ScanTerminalClass.COMPLETED, 3, 75.0),
        (ScanTerminalClass.FAILED, 1, 25.0),
    ]
    assert workload.blocked_boundaries == ()
    assert workload.calculation is not None
    assert workload.calculation.successful_scan_count == 3
    assert workload.calculation.positive_duration_scan_count == 3
    assert workload.calculation.duration_p50_ms == 200.0
    assert workload.calculation.duration_p95_ms == 300.0
    assert workload.calculation.rows_p50 == 20.0
    assert workload.calculation.rows_p95 == 30.0
    assert workload.calculation.rows_per_second_p50 == 100.0
    assert workload.calculation.rows_per_second_p95 == 100.0


def test_direct_oracles_block_incomplete_snapshot_authority() -> None:
    incomplete = replace(_authority_record(), bounded_drain_id=None)

    a01 = oracle_a01_latest_pipeline_snapshot((incomplete,))
    a02 = oracle_a02_scan_outcomes((incomplete,))
    a05 = oracle_a05_scan_workload_duration((incomplete,))

    assert a01.calculation is None
    assert a02.terminal_scan_count is None
    assert a05.calculation is None
    assert a01.blocked_boundaries == ("snapshot[0].bounded_drain_id",)
    assert a02.blocked_boundaries == a01.blocked_boundaries
    assert a05.blocked_boundaries == a01.blocked_boundaries


def test_direct_oracles_deduplicate_identical_immutable_snapshot_events() -> None:
    completed = _authority_record(event_id="completed", duration_ms=200, rows=20)
    failed = _authority_record(
        event_id="failed",
        terminal=(ScanTerminalClass.FAILED, ScanErrorClass.SCAN_ERROR),
        duration_ms=400,
        rows=40,
    )

    outcomes = oracle_a02_scan_outcomes((completed, completed, failed, failed))
    workload = oracle_a05_scan_workload_duration((completed, completed, failed))

    assert [
        (outcome.terminal_class, outcome.terminal_scan_count) for outcome in outcomes.outcomes
    ] == [
        (ScanTerminalClass.COMPLETED, 1),
        (ScanTerminalClass.FAILED, 1),
    ]
    assert workload.calculation is not None
    assert workload.calculation.successful_scan_count == 1
    assert workload.calculation.duration_p50_ms == 200.0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("completed_at_ns", "not-a-nanosecond-timestamp"),
        ("completed_at_ns", True),
        ("duration_ms", 250.0),
        ("rows", True),
        ("payload_schema_version", 1.0),
    ],
)
def test_a01_invalid_scalars_block_without_coercion(field: str, value: object) -> None:
    row = {
        "event_id": "event-1",
        "completed_at_ns": 1_000,
        "payload_schema_version": 1,
        "terminal_class": "completed",
        "duration_ms": 250,
        "error_class": "none",
        "rows": 10,
        "logs": 9,
        "traces": 8,
        "context_events": 7,
        "canonical_activities": 6,
        "source_sessions": 5,
        "pending_outbox": 4,
        "failed_during_drain": 3,
        "bounded_drain_id": "drain-1",
    }
    row[field] = value

    result = oracle_a01_latest_pipeline_snapshot((parse_a01_latest_pipeline_snapshot_row(row),))

    assert result.calculation is None
    assert result.blocked_boundaries


@pytest.mark.parametrize(
    "row",
    [
        {
            "terminal_class": "completed",
            "terminal_scan_count": True,
            "terminal_scan_percent": 100.0,
        },
        {"terminal_class": "completed", "terminal_scan_count": "1", "terminal_scan_percent": 100.0},
        {"terminal_class": "completed", "terminal_scan_count": 1.0, "terminal_scan_percent": 100.0},
    ],
)
def test_a02_parser_rejects_noninteger_counts(row: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="invalid scalar types"):
        parse_a02_scan_outcome_row(row)


@pytest.mark.parametrize(
    "value",
    [True, "1", 1.0],
)
def test_a05_parser_rejects_noninteger_counts(value: object) -> None:
    row = {
        "successful_scan_count": value,
        "positive_duration_scan_count": 1,
        "duration_p50_ms": 250.0,
        "duration_p95_ms": 250.0,
        "rows_p50": 10.0,
        "rows_p95": 10.0,
        "rows_per_second_p50": 40.0,
        "rows_per_second_p95": 40.0,
    }

    with pytest.raises(ValueError, match="invalid counts"):
        parse_a05_scan_workload_duration_row(row)


def test_direct_sql_is_bounded_to_exact_immutable_snapshot_events() -> None:
    for statement in (
        A01_LATEST_PIPELINE_SNAPSHOT_SQL,
        A02_SCAN_OUTCOMES_SQL,
        A05_SCAN_WORKLOAD_DURATION_SQL,
    ):
        assert "attributes_string['event.id'] IN ({event_ids})" in statement
        assert "dashboard_prototype.pipeline_snapshot.v1" in statement
        assert "attributes_string['dashboard.event_kind'] = 'primitive'" in statement
        assert "attributes_string['dashboard.experiment_id'] = 'E-Pipeline-1'" in statement
        assert "timestamp > {start:DateTime64(9, 'UTC')}" in statement
        assert "timestamp <= {end:DateTime64(9, 'UTC')}" in statement
        assert "attributes_string['scan." not in statement

import sqlite3
from datetime import UTC, datetime, timedelta

from experiments.dashboard_prototype.contracts import ExperimentResult
from experiments.dashboard_prototype.experiment_live_common import LiveProofRequest
from experiments.dashboard_prototype.recurrence_live_rule import extract

_SCHEMAS = {
    "versioned_rule_registry": (
        "producer TEXT, surface TEXT, native_session_hash TEXT, rule_id TEXT, "
        "rule_version TEXT, trigger TEXT, required_action TEXT, "
        "observability_condition TEXT, source_time_ns INTEGER, source_order INTEGER, "
        "evidence_id TEXT"
    ),
    "applicability_registry": (
        "producer TEXT, surface TEXT, native_session_hash TEXT, rule_id TEXT, "
        "rule_version TEXT, task_id TEXT, state TEXT, source_time_ns INTEGER, "
        "source_order INTEGER, evidence_id TEXT"
    ),
    "violation_registry": (
        "producer TEXT, surface TEXT, native_session_hash TEXT, rule_id TEXT, "
        "rule_version TEXT, task_id TEXT, state TEXT, observed_action TEXT, "
        "authoritative INTEGER, source_time_ns INTEGER, source_order INTEGER, "
        "evidence_id TEXT"
    ),
}


def _request() -> LiveProofRequest:
    now = datetime(2026, 9, 2, tzinfo=UTC)
    return LiveProofRequest("run:e3", now - timedelta(seconds=1), now)


def _primitives_by_identity(evidence):
    return {
        (row.dimensions["producer"], row.dimensions["stable_identity"]): row
        for row in evidence.primitives
    }


def _create_valid_registries(connection: sqlite3.Connection) -> None:
    for table, schema in _SCHEMAS.items():
        connection.execute(f"CREATE TABLE {table} ({schema})")


def test_empty_database_records_absent_schemas_and_grounded_authority_gaps():
    evidence = extract(sqlite3.connect(":memory:"), _request())

    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.blocked_boundaries == ("missing_durable_authority",)
    assert len(evidence.primitives) == 12
    primitives = _primitives_by_identity(evidence)
    assert all(
        primitives[(producer, registry)].dimensions["capability_state"] == "schema_absent"
        and primitives[(producer, registry)].measures == {"reducer_counts": 1}
        for producer in ("omp", "codex-cli", "codex-app-server")
        for registry in _SCHEMAS
    )
    assert all(
        primitives[(producer, "authority_gap_count")].measures == {"reducer_counts": 3}
        for producer in ("omp", "codex-cli", "codex-app-server")
    )
    assert [row.ordinal for row in evidence.primitives] == list(range(12))


def test_unrelated_tables_are_not_rule_authority():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE skills (guidance TEXT)")
    connection.execute("CREATE TABLE detector_findings (finding TEXT)")

    evidence = extract(connection, _request())

    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.assertions["static_guidance_is_not_authority"] is True
    assert evidence.proof.assertions["detector_findings_are_not_authority"] is True
    assert all(
        row.dimensions["capability_state"] == "schema_absent"
        for row in evidence.primitives
        if row.dimensions["stable_identity"] != "authority_gap_count"
    )


def test_malformed_registry_does_not_produce_an_unseen_zero_count():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE versioned_rule_registry (producer TEXT)")

    evidence = extract(connection, _request())
    primitives = _primitives_by_identity(evidence)

    malformed = primitives[("omp", "versioned_rule_registry")]
    assert malformed.dimensions["capability_state"] == "schema_invalid"
    assert malformed.measures == {"reducer_counts": 1}
    authority_gap = primitives[("omp", "authority_gap_count")]
    assert authority_gap.dimensions["capability_state"] == "schema_invalid"
    assert authority_gap.measures == {"reducer_counts": 3}
    assert evidence.proof.result is ExperimentResult.BLOCKED


def test_valid_empty_registries_report_exact_zero_rows_without_claiming_proven():
    connection = sqlite3.connect(":memory:")
    _create_valid_registries(connection)

    evidence = extract(connection, _request())

    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.assertions["registry_schema_complete"] is True
    assert evidence.proof.assertions["registry_authority_extracted"] is False
    assert all(
        row.measures == {"reducer_counts": 0}
        for row in evidence.primitives
        if row.dimensions["stable_identity"] != "authority_gap_count"
    )
    assert all(
        row.measures == {"reducer_counts": 0}
        for row in evidence.primitives
        if row.dimensions["stable_identity"] == "authority_gap_count"
    )


def test_valid_nonempty_registries_count_each_supported_producer_separately():
    connection = sqlite3.connect(":memory:")
    _create_valid_registries(connection)
    connection.executemany(
        "INSERT INTO versioned_rule_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("omp", "omp", "s", "rule", "v1", "trigger", "action", "condition", 1, 1, "e"),
            ("omp", "omp", "s", "rule", "v2", "trigger", "action", "condition", 2, 2, "e"),
            (
                "codex-cli",
                "codex-cli",
                "s",
                "rule",
                "v1",
                "trigger",
                "action",
                "condition",
                1,
                1,
                "e",
            ),
        ],
    )
    connection.executemany(
        "INSERT INTO applicability_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("omp", "omp", "s", "rule", "v1", "task", "applicable", 1, 1, "e"),
            (
                "codex-app-server",
                "codex-app-server",
                "s",
                "rule",
                "v1",
                "task",
                "applicable",
                1,
                1,
                "e",
            ),
        ],
    )
    connection.execute(
        "INSERT INTO violation_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "codex-app-server",
            "codex-app-server",
            "s",
            "rule",
            "v1",
            "task",
            "satisfied",
            "action",
            1,
            1,
            1,
            "e",
        ),
    )

    evidence = extract(connection, _request())
    primitives = _primitives_by_identity(evidence)

    assert evidence.proof.result is ExperimentResult.BLOCKED
    omp_rule = primitives[("omp", "versioned_rule_registry")]
    codex_cli_rule = primitives[("codex-cli", "versioned_rule_registry")]
    app_server_rule = primitives[("codex-app-server", "versioned_rule_registry")]
    omp_applicability = primitives[("omp", "applicability_registry")]
    app_server_applicability = primitives[("codex-app-server", "applicability_registry")]
    app_server_violation = primitives[("codex-app-server", "violation_registry")]
    assert omp_rule.measures == {"reducer_counts": 2}
    assert codex_cli_rule.measures == {"reducer_counts": 1}
    assert app_server_rule.measures == {"reducer_counts": 0}
    assert omp_applicability.measures == {"reducer_counts": 1}
    assert app_server_applicability.measures == {"reducer_counts": 1}
    assert app_server_violation.measures == {"reducer_counts": 1}
    assert all(
        set(row.dimensions) <= {"producer", "surface", "stable_identity", "capability_state"}
        and set(row.measures) <= {"reducer_counts"}
        for row in evidence.primitives
    )

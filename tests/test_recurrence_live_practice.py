import sqlite3
from datetime import UTC, datetime, timedelta

from experiments.dashboard_prototype.contracts import ExperimentResult
from experiments.dashboard_prototype.experiment_live_common import LiveProofRequest
from experiments.dashboard_prototype.recurrence_live_practice import extract


def request() -> LiveProofRequest:
    start = datetime(2026, 9, 2, tzinfo=UTC)
    return LiveProofRequest("recurrence-practice-live", start, start + timedelta(minutes=5))


def test_live_audit_blocks_and_emits_twelve_authority_gap_primitives() -> None:
    evidence = extract(sqlite3.connect(":memory:"), request())
    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.metrics == {"producer_count": 3}
    assert len(evidence.primitives) == 12
    assert [
        (row.dimensions["producer"], row.dimensions["surface"]) for row in evidence.primitives[::4]
    ] == [
        ("omp", "omp"),
        ("codex-cli", "codex-cli"),
        ("codex-app-server", "codex-app-server"),
    ]
    assert [row.ordinal for row in evidence.primitives] == list(range(12))
    assert {row.dimensions["stable_identity"] for row in evidence.primitives} == {
        "ordered_operation_count",
        "explicit_terminal_count",
        "task_class_count",
        "registry_authority_count",
    }
    assert all(
        row.dimensions["capability_state"] == "authority_unavailable" for row in evidence.primitives
    )
    assert all(row.measures == {"reducer_counts": 1} for row in evidence.primitives)
    assert all(
        set(row.dimensions) <= {"producer", "surface", "capability_state", "stable_identity"}
        and set(row.measures) == {"reducer_counts"}
        for row in evidence.primitives
    )


def test_canonical_aggregate_schema_cannot_fabricate_successful_practice() -> None:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE canonical_activities (id TEXT, producer TEXT, operation_kind TEXT);
        CREATE TABLE canonical_findings (id TEXT, finding_kind TEXT);
        """
    )
    evidence = extract(connection, request())
    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.metrics == {"producer_count": 3}
    assert all(row.measures["reducer_counts"] == 1 for row in evidence.primitives)
    assert all(
        row.dimensions["capability_state"] == "authority_unavailable" for row in evidence.primitives
    )


def test_phase4_table_names_alone_cannot_create_successful_practice() -> None:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE task_operation_evidence (ignored TEXT);
        CREATE TABLE task_terminal_evidence (ignored TEXT);
        CREATE TABLE task_registry_versions (ignored TEXT);
        """
    )
    evidence = extract(connection, request())
    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.assertions["terminal_success_not_inferred"] is True
    assert all(row.measures["reducer_counts"] == 1 for row in evidence.primitives)


def test_authoritative_boundary_not_extracted_emits_only_audited_gaps() -> None:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE task_operation_evidence (
            producer TEXT, attempt_hash TEXT, canonical_task_id TEXT, source_order TEXT,
            operation_fingerprint TEXT, target_fingerprint TEXT, task_class TEXT,
            occurred_at TEXT, authority_evidence_id TEXT
        );
        CREATE TABLE task_terminal_evidence (
            producer TEXT, attempt_hash TEXT, terminal_outcome TEXT, occurred_at TEXT,
            authority_evidence_id TEXT
        );
        CREATE TABLE task_registry_versions (
            sequence_fingerprint TEXT, sequence_version TEXT, owner_state TEXT,
            owner_version TEXT, authority_evidence_id TEXT
        );
        """
    )
    evidence = extract(connection, request())
    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert all(
        row.dimensions["capability_state"] == "authority_not_extracted"
        for row in evidence.primitives
    )
    assert all(row.measures == {"reducer_counts": 1} for row in evidence.primitives)

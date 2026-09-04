import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from experiments.dashboard_prototype import recurrence_execution as execution
from experiments.dashboard_prototype.contracts import ExperimentResult
from experiments.dashboard_prototype.experiment_live_common import LiveProofRequest
from experiments.dashboard_prototype.recurrence_live_finding import extract


def request() -> LiveProofRequest:
    start = datetime(2026, 3, 25, tzinfo=UTC)
    return LiveProofRequest("recurrence.live.1", start, start + timedelta(days=7))


def schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE findings (
          id TEXT, fingerprint TEXT, detector_id TEXT, detector_version INTEGER,
          entity_version INTEGER, is_active INTEGER, replaced_by_finding_id TEXT,
          evidence_start_ns INTEGER, evidence_end_ns INTEGER, first_seen_ns INTEGER,
          last_seen_ns INTEGER, occurrence_count INTEGER, canonical_task_count INTEGER,
          local_day_count INTEGER
        );
        CREATE TABLE canonical_finding_membership (
          finding_id TEXT, activity_id TEXT, activity_version INTEGER, canonical_task_id TEXT,
          rationale TEXT, created_at TEXT
        );
        CREATE TABLE canonical_activities (
          id TEXT, producer TEXT, producer_surface TEXT, canonical_task_id TEXT,
          source_ended_at_ns INTEGER, source_membership_hash TEXT, source_membership_json TEXT
        );
        CREATE TABLE canonical_activity_versions (
          activity_id TEXT, version INTEGER, attribution_state TEXT, project_identity_id TEXT
        );
        """
    )


@dataclass(frozen=True, slots=True)
class FindingSpec:
    version: int = 1
    active: int = 1
    replacement: str | None = None
    occurrence_count: int = 1
    task_count: int = 1


DEFAULT_FINDING_SPEC = FindingSpec()


def finding(
    connection: sqlite3.Connection,
    identifier: str,
    fingerprint: str,
    state: FindingSpec = DEFAULT_FINDING_SPEC,
) -> None:
    start = 1_774_396_800_000_000_000
    end = start + 604_800_000_000_000
    connection.execute(
        "INSERT INTO findings VALUES (?, ?, 'detector', 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
        (
            identifier,
            fingerprint,
            state.version,
            state.active,
            state.replacement,
            start,
            end,
            start,
            end,
            state.occurrence_count,
            state.task_count,
        ),
    )


def member(connection: sqlite3.Connection, finding_id: str, activity_id: str, task_id: str) -> None:
    connection.execute(
        "INSERT INTO canonical_finding_membership VALUES (?, ?, 1, ?, 'rule', 'now')",
        (finding_id, activity_id, task_id),
    )
    connection.execute(
        "INSERT INTO canonical_activities VALUES (?, 'omp', 'omp', ?, 1, 'hash', 'json')",
        (activity_id, task_id),
    )
    connection.execute(
        "INSERT INTO canonical_activity_versions VALUES (?, 1, 'resolved', 'project')",
        (activity_id,),
    )


def test_missing_schema_blocks_without_a_synthetic_zero_denominator() -> None:
    evidence = extract(sqlite3.connect(":memory:"), request())
    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.metrics == {"authority_available": 0, "contradiction_count": 0}
    assert evidence.proof.assertions["durable_window_bounds_authoritative"] is False


def test_bounded_population_reconciles_exact_task_membership() -> None:
    connection = sqlite3.connect(":memory:")
    schema(connection)
    finding(connection, "f1", "a" * 64, FindingSpec(occurrence_count=2, task_count=2))
    member(connection, "f1", "activity1", "task1")
    member(connection, "f1", "activity2", "task2")
    evidence = extract(connection, request())
    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.metrics["selected_finding_rows"] == 1
    assert evidence.proof.metrics["selected_membership_rows"] == 2
    assert evidence.proof.assertions["selected_membership_denominator_reconciled"] is True
    assert evidence.proof.assertions["membership_task_identity_authoritative"] is True
    assert evidence.primitives
    assert all(type(row.measures["reducer_counts"]) is float for row in evidence.primitives)
    assert len(evidence.finding_version_source_ids) == 1
    assert len(evidence.canonical_task_membership_source_ids) == 2
    assert set(evidence.proof.evidence_ids) == {
        *evidence.finding_version_source_ids,
        *evidence.canonical_task_membership_source_ids,
    }
    extract_window = execution.ExtractionWindow(request().start, request().end)
    primitives = execution.primitive_events((evidence,), request().run_id, extract_window)
    local = execution._local_oracle(primitives)
    finalized = execution._final_proofs((evidence,), local, primitives)
    assert finalized[0].result is ExperimentResult.PROVEN


def test_missing_evidence_bounds_blocks_rather_than_selecting_the_row() -> None:
    connection = sqlite3.connect(":memory:")
    schema(connection)
    finding(connection, "f1", "a" * 64)
    connection.execute("UPDATE findings SET evidence_start_ns = NULL")
    evidence = extract(connection, request())
    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.assertions["durable_window_bounds_authoritative"] is False


def test_task_membership_mismatch_fails_closed() -> None:
    connection = sqlite3.connect(":memory:")
    schema(connection)
    finding(connection, "f1", "a" * 64)
    member(connection, "f1", "activity1", "task1")
    connection.execute("UPDATE canonical_activities SET canonical_task_id = 'task2'")
    evidence = extract(connection, request())
    assert evidence.proof.result is ExperimentResult.FAILED
    assert evidence.proof.assertions["membership_task_identity_authoritative"] is False


def test_immutable_supersession_requires_same_identity_and_increasing_version() -> None:
    connection = sqlite3.connect(":memory:")
    schema(connection)
    finding(connection, "old", "a" * 64, FindingSpec(version=1, active=0, replacement="new"))
    finding(connection, "new", "a" * 64, FindingSpec(version=2))
    assert extract(connection, request()).proof.result is ExperimentResult.BLOCKED
    connection.execute("UPDATE findings SET entity_version = 1 WHERE id = 'new'")
    evidence = extract(connection, request())
    assert evidence.proof.result is ExperimentResult.FAILED
    assert evidence.proof.assertions["lineage_shape_valid"] is False


def test_active_duplicate_fingerprint_fails_closed() -> None:
    connection = sqlite3.connect(":memory:")
    schema(connection)
    finding(connection, "f1", "a" * 64)
    finding(connection, "f2", "a" * 64)
    evidence = extract(connection, request())
    assert evidence.proof.result is ExperimentResult.FAILED
    assert evidence.proof.assertions["duplicate_fingerprint_identity_absent"] is False

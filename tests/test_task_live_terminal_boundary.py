import sqlite3
from datetime import UTC, datetime, timedelta, timezone

from experiments.dashboard_prototype.contracts import EvidenceProvenance, ExperimentResult
from experiments.dashboard_prototype.experiment_live_common import LiveProofRequest
from experiments.dashboard_prototype.task_live_terminal_boundary import extract

START = datetime(2026, 9, 2, 12, tzinfo=UTC)


def _request() -> LiveProofRequest:
    return LiveProofRequest("task-terminal-live", START, START + timedelta(minutes=1))


def _database(*, legacy: bool = False, malformed_identity: bool = False) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    columns = (
        "event_id TEXT, producer TEXT, session_id TEXT, event_type TEXT, "
        "occurred_at TEXT, project_id TEXT"
    )
    if not legacy:
        columns += ", project_name TEXT, project_root TEXT, project_kind TEXT"
    connection.execute(f"CREATE TABLE session_context_events ({columns})")
    for ordinal, (producer, occurred_at) in enumerate(
        (
            ("omp", START),
            ("codex-cli", START + timedelta(seconds=1)),
            ("codex-app-server", START + timedelta(minutes=1)),
        )
    ):
        values = (
            "not-a-hash" if malformed_identity and ordinal == 1 else f"{ordinal + 1:064x}",
            producer,
            "safe-session",
            "session_context" if producer == "codex-cli" else "session_start",
            occurred_at.isoformat(),
            f"{ordinal + 4:064x}",
        )
        if legacy:
            connection.execute(
                "INSERT INTO session_context_events VALUES (?, ?, ?, ?, ?, ?)", values
            )
        else:
            connection.execute(
                "INSERT INTO session_context_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*values, "safe-project", "/safe-root", "git"),
            )
    return connection


def test_live_extract_is_fresh_real_blocked_with_zero_terminal_candidates() -> None:
    evidence = extract(_database(), _request())

    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.provenance is EvidenceProvenance.FRESH_REAL
    assert evidence.primitives == ()
    assert evidence.proof.metrics["terminal_candidate_population"] == 0
    assert evidence.proof.metrics["source_authority_present"] == 1
    assert evidence.remote_oracle["terminal-boundary"]["terminal_candidate_population"] == 0
    assert evidence.remote_oracle["terminal-boundary"]["observed_producer_population"] == 2


def test_live_extract_uses_utc_open_closed_source_interval() -> None:
    offset = timezone(timedelta(hours=5))
    request = LiveProofRequest(
        "task-terminal-offset",
        START.astimezone(offset),
        (START + timedelta(minutes=1)).astimezone(offset),
    )

    evidence = extract(_database(), request)

    assert evidence.remote_oracle["terminal-boundary"]["observed_producer_population"] == 2


def test_legacy_schema_cannot_claim_fresh_real_source_authority() -> None:
    evidence = extract(_database(legacy=True), _request())

    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.provenance is EvidenceProvenance.RETAINED
    assert evidence.proof.metrics["source_authority_present"] == 0
    assert evidence.remote_oracle["terminal-boundary"]["context_schema_present"] == 0
    assert evidence.remote_oracle["terminal-boundary"]["terminal_candidate_population"] == 0


def test_empty_canonical_schema_cannot_claim_fresh_real_source_authority() -> None:
    connection = _database()
    connection.execute("DELETE FROM session_context_events")
    evidence = extract(connection, _request())

    assert evidence.proof.provenance is EvidenceProvenance.RETAINED
    assert evidence.proof.metrics["source_authority_present"] == 0
    assert evidence.remote_oracle["terminal-boundary"]["observed_producer_population"] == 0


def test_invalid_producer_event_pairing_cannot_claim_fresh_real_source_authority() -> None:
    connection = _database()
    connection.execute(
        "UPDATE session_context_events SET event_type = 'session_context' "
        "WHERE producer = 'codex-app-server'"
    )
    evidence = extract(connection, _request())

    assert evidence.proof.provenance is EvidenceProvenance.RETAINED
    assert evidence.proof.metrics["source_authority_present"] == 0


def test_malformed_time_cannot_claim_fresh_real_source_authority() -> None:
    connection = _database()
    connection.execute(
        "UPDATE session_context_events SET occurred_at = 'not-a-timestamp' "
        "WHERE producer = 'codex-app-server'"
    )
    evidence = extract(connection, _request())

    assert evidence.proof.provenance is EvidenceProvenance.RETAINED
    assert evidence.proof.metrics["source_authority_present"] == 0


def test_malformed_project_tuple_cannot_claim_fresh_real_source_authority() -> None:
    connection = _database()
    connection.execute(
        "UPDATE session_context_events SET project_root = 'relative-root' "
        "WHERE producer = 'codex-app-server'"
    )
    evidence = extract(connection, _request())

    assert evidence.proof.provenance is EvidenceProvenance.RETAINED
    assert evidence.proof.metrics["source_authority_present"] == 0


def test_malformed_context_identity_cannot_claim_fresh_real_source_authority() -> None:
    evidence = extract(_database(malformed_identity=True), _request())

    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.provenance is EvidenceProvenance.RETAINED
    assert evidence.proof.metrics["source_authority_present"] == 0
    assert evidence.remote_oracle["terminal-boundary"]["observed_producer_population"] == 0

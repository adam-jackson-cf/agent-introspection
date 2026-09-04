import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta

from experiments.dashboard_prototype.attribution_live_common import LiveProofRequest
from experiments.dashboard_prototype.attribution_live_late_context import extract
from experiments.dashboard_prototype.contracts import ExperimentResult

START = datetime(2026, 9, 1, tzinfo=UTC)
END = START + timedelta(hours=1)


def _database(*, lifecycle: bool = True, event_id: str | None = None) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE canonical_activities (
            id TEXT, producer TEXT, producer_surface TEXT, correlation_id TEXT,
            source_ended_at_ns INTEGER
        );
        CREATE TABLE canonical_activity_versions (
            activity_id TEXT, version INTEGER, attribution_state TEXT,
            attribution_method TEXT, reason_code TEXT, project_identity_id TEXT
        );
        CREATE TABLE canonical_activity_outbox_evidence (
            activity_id TEXT, activity_version INTEGER, payload_schema_version INTEGER,
            event_name TEXT, event_id TEXT
        );
        CREATE TABLE session_context_intervals (
            event_id TEXT, producer TEXT, session_id TEXT, started_at TEXT, ended_at TEXT
        );
        """
    )
    source = START + timedelta(minutes=30)
    connection.execute(
        "INSERT INTO canonical_activities VALUES (?, ?, ?, ?, ?)",
        (
            "stable-activity",
            "omp",
            "omp",
            "native-session",
            int(source.timestamp() * 1_000_000_000),
        ),
    )
    connection.executemany(
        "INSERT INTO canonical_activity_versions VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("stable-activity", 1, "unresolved", "lifecycle", "missing_context", None),
            ("stable-activity", 2, "resolved", "lifecycle", None, "a" * 64),
        ],
    )
    canonical_event_id = event_id or _event_id(2)
    connection.executemany(
        "INSERT INTO canonical_activity_outbox_evidence VALUES (?, ?, ?, ?, ?)",
        [
            ("stable-activity", 1, 2, "introspection.activity.version.recorded", _event_id(1)),
            (
                "stable-activity",
                2,
                2,
                "introspection.activity.version.recorded",
                canonical_event_id,
            ),
        ],
    )
    if lifecycle:
        connection.execute(
            "INSERT INTO session_context_intervals VALUES (?, ?, ?, ?, ?)",
            ("interval", "omp", "native-session", START.isoformat(), END.isoformat()),
        )
    return connection


def _event_id(version: int) -> str:
    return hashlib.sha256(
        "\x1f".join(
            ("stable-activity", str(version), "2", "introspection.activity.version.recorded")
        ).encode()
    ).hexdigest()


def test_extracts_all_versions_for_selected_identity_but_blocks_unreconciled_rate() -> None:
    evidence = extract(_database(), LiveProofRequest("run-1", START, END))

    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.metrics["transition_count"] == 1
    assert evidence.proof.metrics["ever_unresolved_count"] == 1
    assert len(evidence.primitives) == 1
    assert set(evidence.primitives[0].dimensions) >= {"resolved_event_digest"}
    assert sum(cohort["transition_count"] for cohort in evidence.remote_oracle.values()) == len(
        evidence.primitives
    )
    serialized = repr(evidence)
    assert "native-session" not in serialized
    assert "stable-activity" not in serialized


def test_missing_lifecycle_authority_fails_closed() -> None:
    evidence = extract(_database(lifecycle=False), LiveProofRequest("run-1", START, END))

    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert "late-context.lifecycle-authority" in evidence.proof.blocked_boundaries
    assert evidence.primitives == ()


def test_malformed_deterministic_event_id_fails_closed() -> None:
    evidence = extract(
        _database(event_id="not-the-canonical-event-id"),
        LiveProofRequest("run-1", START, END),
    )

    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert "late-context.lifecycle-authority" in evidence.proof.blocked_boundaries
    assert evidence.primitives == ()

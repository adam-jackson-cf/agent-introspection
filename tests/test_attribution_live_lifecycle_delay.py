from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from experiments.dashboard_prototype.attribution_live_common import LiveProofRequest
from experiments.dashboard_prototype.attribution_live_lifecycle_delay import extract
from experiments.dashboard_prototype.contracts import ExperimentResult

START = datetime(2026, 9, 1, tzinfo=UTC)
END = START + timedelta(minutes=10)


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE source_session_records (
            source_kind TEXT, service_name TEXT, source_id TEXT, source_timestamp TEXT,
            terminal_outcome TEXT, context_evidence_id TEXT, session_ids_json TEXT,
            thread_ids_json TEXT
        );
        CREATE TABLE session_context_events (
            event_id TEXT, producer TEXT, session_id TEXT, occurred_at TEXT
        );
        CREATE TABLE session_context_intervals (
            event_id TEXT, producer TEXT, session_id TEXT, started_at TEXT, ended_at TEXT
        );
        """
    )
    return connection


def _request() -> LiveProofRequest:
    return LiveProofRequest("attribution-delay", START, END)


def _insert_authority(connection: sqlite3.Connection, session: str = "session") -> None:
    connection.execute(
        "INSERT INTO session_context_events VALUES ('context', 'omp', ?, ?)",
        (session, START.isoformat()),
    )
    connection.execute(
        "INSERT INTO session_context_intervals VALUES ('interval', 'omp', ?, ?, ?)",
        (session, (START - timedelta(seconds=1)).isoformat(), END.isoformat()),
    )


def _insert_source(
    connection: sqlite3.Connection,
    source_id: str,
    timestamp: datetime,
    outcome: str = "attributed",
) -> None:
    connection.execute(
        (
            "INSERT INTO source_session_records VALUES "
            "('log', 'omp', ?, ?, ?, 'context', '[\"session\"]', '[]')"
        ),
        (source_id, timestamp.isoformat(), outcome),
    )


def test_extracts_first_exact_authoritative_source_and_hashes_primitive_identity() -> None:
    connection = _connection()
    _insert_authority(connection)
    _insert_source(connection, "late", START + timedelta(seconds=2))
    _insert_source(connection, "first", START + timedelta(seconds=1))
    evidence = extract(connection, _request())
    assert evidence.proof.result is ExperimentResult.PROVEN
    assert evidence.proof.metrics["matched_sessions"] == 1
    assert len(evidence.primitives) == 1
    primitive = evidence.primitives[0]
    evidence_identity = primitive.dimensions["evidence"]
    assert isinstance(evidence_identity, str)
    assert len(evidence_identity) == 64
    assert "session" not in repr(primitive)


def test_missing_exact_context_linkage_blocks() -> None:
    connection = _connection()
    _insert_authority(connection)
    connection.execute(
        (
            "INSERT INTO source_session_records VALUES "
            "('log', 'omp', 'source', ?, 'attributed', 'context', '[\"other\"]', '[]')"
        ),
        ((START + timedelta(seconds=1)).isoformat(),),
    )
    connection.execute(
        (
            "INSERT INTO source_session_records VALUES "
            "('log', 'omp', 'source-2', ?, 'attributed', 'context', '[\"other-2\"]', '[]')"
        ),
        ((START + timedelta(seconds=2)).isoformat(),),
    )
    evidence = extract(connection, _request())
    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.blocked_boundaries == (
        "authoritative-source-session",
        "context-event-linkage:omp:omp:count=2",
    )


def test_loads_interval_started_before_range_for_containment() -> None:
    connection = _connection()
    _insert_authority(connection)
    _insert_source(connection, "source", START + timedelta(seconds=1))
    evidence = extract(connection, _request())
    assert evidence.proof.result is ExperimentResult.PROVEN


def test_raw_unresolved_source_is_selected_without_project_attribution() -> None:
    connection = _connection()
    _insert_authority(connection)
    _insert_source(connection, "raw-source", START + timedelta(seconds=1), "blocked")
    evidence = extract(connection, _request())
    assert evidence.proof.result is ExperimentResult.PROVEN
    assert evidence.proof.metrics["matched_sessions"] == 1


def test_open_accepted_interval_contains_later_source() -> None:
    connection = _connection()
    _insert_authority(connection)
    connection.execute("UPDATE session_context_intervals SET ended_at = NULL")
    _insert_source(connection, "open-source", START + timedelta(seconds=1))
    evidence = extract(connection, _request())
    assert evidence.proof.result is ExperimentResult.PROVEN


def test_no_source_selections_blocks_without_unreproducible_oracle_cohorts() -> None:
    connection = _connection()
    _insert_authority(connection)
    evidence = extract(connection, _request())
    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.metrics["selected_sessions"] == 0
    assert evidence.primitives == ()
    assert dict(evidence.remote_oracle) == {}

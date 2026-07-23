from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_introspection.migrations import apply_migrations
from agent_introspection.session_context import (
    SessionContextError,
    correlated_project,
    drain_inbox,
    event_payload,
    parse_event,
    spool_event,
)


def _connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    apply_migrations(connection, path)
    return connection


def _event(root: Path, event_type: str, moment: datetime):
    project_id = hashlib.sha256(root.as_posix().encode()).hexdigest()
    event_id = hashlib.sha256(
        f"{event_type}:{moment.isoformat()}:{project_id}".encode()
    ).hexdigest()
    return parse_event(
        {
            "event_id": event_id,
            "producer": "claude-code",
            "session_id": "session-1",
            "event_type": event_type,
            "occurred_at": moment.isoformat(),
            "agent": {
                "project": {
                    "id": project_id,
                    "name": root.name,
                    "root": root.as_posix(),
                    "kind": "non_git",
                }
            },
        }
    )


def test_context_lifecycle_is_idempotent_immutable_and_correlates_exact_session(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ledger.sqlite3"
    root = tmp_path / "project"
    root.mkdir()
    connection = _connection(database)
    try:
        start_at = datetime(2026, 1, 1, tzinfo=UTC)
        start = _event(root, "session_start", start_at)
        spool_event(start, directory=tmp_path / "inbox")
        assert len(drain_inbox(connection, directory=tmp_path / "inbox")) == 1
        assert drain_inbox(connection, directory=tmp_path / "inbox") == ()
        project = correlated_project(
            connection,
            producer="claude-code",
            session_id="session-1",
            started_at=start_at,
            ended_at=start_at + timedelta(seconds=1),
        )
        assert project is not None and project.identity == start.project.identity
        assert (
            correlated_project(
                connection,
                producer="claude-code",
                session_id="different-session",
                started_at=start_at,
                ended_at=start_at,
            )
            is None
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE session_context_events SET session_id = 'other'")
    finally:
        connection.close()


def test_reused_event_id_with_mismatched_canonical_record_remains_inbox_unresolved(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ledger.sqlite3"
    root = tmp_path / "project"
    root.mkdir()
    connection = _connection(database)
    try:
        inbox = tmp_path / "inbox"
        event = _event(root, "session_start", datetime(2026, 1, 1, tzinfo=UTC))
        spool_event(event, directory=inbox)
        assert len(drain_inbox(connection, directory=inbox)) == 1
        payload = event_payload(event)
        duplicate_path = inbox / f"{event.event_id}.json"
        duplicate_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
        assert drain_inbox(connection, directory=inbox) == ()
        assert not duplicate_path.exists()
        payload["session_id"] = "reused-id-different-session"
        duplicate_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
        ledger_before = connection.execute(
            """
            SELECT producer, session_id, event_type, occurred_at, project_id,
                   project_name, project_root, project_kind
            FROM session_context_events WHERE event_id = ?
            """,
            (event.event_id,),
        ).fetchone()
        outbox_before = connection.execute("SELECT COUNT(*) FROM otlp_outbox").fetchone()

        assert drain_inbox(connection, directory=inbox) == ()

        assert duplicate_path.exists()
        assert (
            connection.execute(
                """
            SELECT producer, session_id, event_type, occurred_at, project_id,
                   project_name, project_root, project_kind
            FROM session_context_events WHERE event_id = ?
            """,
                (event.event_id,),
            ).fetchone()
            == ledger_before
        )
        assert connection.execute("SELECT COUNT(*) FROM otlp_outbox").fetchone() == outbox_before
    finally:
        connection.close()


def test_malformed_and_conflicting_records_remain_unresolved(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite3"
    root = tmp_path / "project"
    root.mkdir()
    connection = _connection(database)
    try:
        inbox = tmp_path / "inbox"
        start = _event(root, "session_start", datetime(2026, 1, 1, tzinfo=UTC))
        spool_event(start, directory=inbox)
        conflict = _event(root, "session_start", datetime(2026, 1, 1, 0, 1, tzinfo=UTC))
        spool_event(conflict, directory=inbox)
        (inbox / ("0" * 64 + ".json")).write_text("{}")
        drain_inbox(connection, directory=inbox)
        assert connection.execute("SELECT COUNT(*) FROM session_context_events").fetchone()[0] == 1
        assert (inbox / ("0" * 64 + ".json")).exists()
    finally:
        connection.close()


def test_event_schema_rejects_missing_and_noncanonical_fields(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    event = _event(root, "session_start", datetime(2026, 1, 1, tzinfo=UTC))
    payload = event_payload(event)
    payload.pop("session_id")
    with pytest.raises(SessionContextError):
        parse_event(payload)
    payload = event_payload(event)
    payload["event_id"] = hashlib.sha256(b"upper").hexdigest().upper()
    with pytest.raises(SessionContextError):
        parse_event(payload)

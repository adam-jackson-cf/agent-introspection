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


def _event(root: Path, event_type: str, moment: datetime, *, event_id: str | None = None):
    project_id = hashlib.sha256(root.as_posix().encode()).hexdigest()
    if event_id is None:
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
                    "kind": "git",
                }
            },
        }
    )


@pytest.mark.parametrize("producer", ("claude-code", "codex-cli", "codex-app-server", "omp"))
def test_context_parser_accepts_only_canonical_producers(tmp_path: Path, producer: str) -> None:
    event = _event(tmp_path / "project", "session_start", datetime(2026, 1, 1, tzinfo=UTC))
    payload = event_payload(event)
    payload["producer"] = producer

    assert parse_event(payload).producer == producer


@pytest.mark.parametrize("event_type", ("session_start", "workspace_changed", "session_end"))
def test_context_parser_accepts_exact_canonical_event_types(
    tmp_path: Path, event_type: str
) -> None:
    assert (
        parse_event(
            event_payload(
                _event(tmp_path / "project", event_type, datetime(2026, 1, 1, tzinfo=UTC))
            )
        ).event_type
        == event_type
    )


def test_context_lifecycle_transitions_immutable_intervals_and_correlates_projects(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ledger.sqlite3"
    first_root = tmp_path / "first-project"
    second_root = tmp_path / "second-project"
    first_root.mkdir()
    second_root.mkdir()
    connection = _connection(database)
    try:
        start_at = datetime(2026, 1, 1, tzinfo=UTC)
        changed_at = start_at + timedelta(minutes=1)
        start = _event(first_root, "session_start", start_at, event_id="f" * 64)
        changed = _event(second_root, "workspace_changed", changed_at, event_id="0" * 64)
        inbox = tmp_path / "inbox"
        spool_event(start, directory=inbox)
        spool_event(changed, directory=inbox)

        accepted = drain_inbox(connection, directory=inbox)
        assert [event.entity_id for event in accepted] == [start.event_id, changed.event_id]
        assert len(accepted) == 2
        assert drain_inbox(connection, directory=inbox) == ()
        assert connection.execute(
            """
            SELECT event_id, started_at, ended_at, end_event_id, project_id
            FROM session_context_intervals ORDER BY started_at
            """
        ).fetchall() == [
            (
                start.event_id,
                start_at.isoformat(),
                changed_at.isoformat(),
                changed.event_id,
                start.project.identity,
            ),
            (
                changed.event_id,
                changed_at.isoformat(),
                None,
                None,
                changed.project.identity,
            ),
        ]
        before = correlated_project(
            connection,
            producer="claude-code",
            session_id="session-1",
            started_at=start_at + timedelta(seconds=1),
            ended_at=changed_at - timedelta(seconds=1),
        )
        after = correlated_project(
            connection,
            producer="claude-code",
            session_id="session-1",
            started_at=changed_at,
            ended_at=changed_at + timedelta(seconds=1),
        )
        assert before is not None and before.identity == start.project.identity
        assert after is not None and after.identity == changed.project.identity
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
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE session_context_intervals SET ended_at = ? WHERE event_id = ?",
                (changed_at.isoformat(), start.event_id),
            )
    finally:
        connection.close()


def test_reused_event_id_conflict_remains_unresolved_without_mutating_ledger_or_outbox(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ledger.sqlite3"
    root = tmp_path / "project"
    root.mkdir()
    connection = _connection(database)
    try:
        inbox = tmp_path / "inbox"
        start = _event(root, "session_start", datetime(2026, 1, 1, tzinfo=UTC))
        spool_event(start, directory=inbox)
        assert len(drain_inbox(connection, directory=inbox)) == 1

        payload = event_payload(start)
        conflict_path = inbox / f"{start.event_id}.json"
        conflict_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
        assert drain_inbox(connection, directory=inbox) == ()
        assert not conflict_path.exists()

        payload["session_id"] = "reused-id-different-session"
        conflict_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
        ledger_before = connection.execute(
            """
            SELECT producer, session_id, event_type, occurred_at, project_id,
                   project_name, project_root, project_kind
            FROM session_context_events WHERE event_id = ?
            """,
            (start.event_id,),
        ).fetchone()
        outbox_before = connection.execute("SELECT COUNT(*) FROM otlp_outbox").fetchone()

        assert drain_inbox(connection, directory=inbox) == ()

        assert conflict_path.exists()
        assert (
            connection.execute(
                """
            SELECT producer, session_id, event_type, occurred_at, project_id,
                   project_name, project_root, project_kind
            FROM session_context_events WHERE event_id = ?
            """,
                (start.event_id,),
            ).fetchone()
            == ledger_before
        )
        assert connection.execute("SELECT COUNT(*) FROM otlp_outbox").fetchone() == outbox_before
        assert connection.execute("SELECT COUNT(*) FROM canonical_rejections").fetchone() == (0,)
    finally:
        connection.close()


def test_ordered_replay_and_end_exclusive_correlation(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite3"
    first_root = tmp_path / "first-project"
    second_root = tmp_path / "second-project"
    first_root.mkdir()
    second_root.mkdir()
    connection = _connection(database)
    try:
        inbox = tmp_path / "inbox"
        start_at = datetime(2026, 1, 1, tzinfo=UTC)
        changed_at = start_at + timedelta(minutes=1)
        session_end = changed_at + timedelta(minutes=1)
        start = _event(first_root, "session_start", start_at)
        changed = _event(second_root, "workspace_changed", changed_at)
        ended = _event(second_root, "session_end", session_end)
        spool_event(ended, directory=inbox)
        spool_event(changed, directory=inbox)
        spool_event(start, directory=inbox)

        accepted = drain_inbox(connection, directory=inbox)
        assert [event.entity_id for event in accepted] == [
            start.event_id,
            changed.event_id,
            ended.event_id,
        ]
        assert (
            correlated_project(
                connection,
                producer="claude-code",
                session_id="session-1",
                started_at=start_at,
                ended_at=changed_at,
            ).identity
            == start.project.identity
        )
        assert (
            correlated_project(
                connection,
                producer="claude-code",
                session_id="session-1",
                started_at=changed_at,
                ended_at=session_end,
            ).identity
            == changed.project.identity
        )
        assert (
            correlated_project(
                connection,
                producer="claude-code",
                session_id="session-1",
                started_at=session_end,
                ended_at=session_end + timedelta(seconds=1),
            )
            is None
        )
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
    payload = event_payload(event)
    payload["event_type"] = "workspace_change"
    with pytest.raises(SessionContextError):
        parse_event(payload)
    payload = event_payload(event)
    payload["agent"]["project"]["kind"] = "non_git"
    with pytest.raises(SessionContextError):
        parse_event(payload)


def test_late_lifecycle_event_replays_within_configured_clock_skew(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite3"
    first_root = tmp_path / "first-project"
    second_root = tmp_path / "second-project"
    first_root.mkdir()
    second_root.mkdir()
    connection = _connection(database)
    try:
        inbox = tmp_path / "inbox"
        started_at = datetime(2026, 1, 1, tzinfo=UTC)
        changed_at = started_at + timedelta(minutes=5)
        ended_at = started_at + timedelta(minutes=10)
        start = _event(first_root, "session_start", started_at)
        ended = _event(first_root, "session_end", ended_at)
        changed = _event(second_root, "workspace_changed", changed_at)
        spool_event(start, directory=inbox)
        assert [item.entity_id for item in drain_inbox(connection, directory=inbox)] == [
            start.event_id
        ]
        spool_event(ended, directory=inbox)
        assert [item.entity_id for item in drain_inbox(connection, directory=inbox)] == [
            ended.event_id
        ]
        spool_event(changed, directory=inbox)
        assert [
            item.entity_id
            for item in drain_inbox(connection, directory=inbox, clock_skew_seconds=600)
        ] == [changed.event_id]
        assert connection.execute(
            "SELECT project_id, started_at, ended_at FROM session_context_intervals "
            "ORDER BY started_at"
        ).fetchall() == [
            (start.project.identity, started_at.isoformat(), changed_at.isoformat()),
            (changed.project.identity, changed_at.isoformat(), ended_at.isoformat()),
        ]
    finally:
        connection.close()


def test_late_lifecycle_event_beyond_clock_skew_is_quarantined(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite3"
    root = tmp_path / "project"
    root.mkdir()
    connection = _connection(database)
    try:
        inbox = tmp_path / "inbox"
        started_at = datetime(2026, 1, 1, tzinfo=UTC)
        start = _event(root, "session_start", started_at)
        ended = _event(root, "session_end", started_at + timedelta(minutes=10))
        late = _event(root, "workspace_changed", started_at + timedelta(minutes=5))
        spool_event(start, directory=inbox)
        drain_inbox(connection, directory=inbox)
        spool_event(ended, directory=inbox)
        drain_inbox(connection, directory=inbox)
        spool_event(late, directory=inbox)
        assert drain_inbox(connection, directory=inbox, clock_skew_seconds=299) == ()
        assert connection.execute("SELECT reason_code FROM canonical_rejections").fetchall() == [
            ("clock_skew_exceeded",)
        ]
    finally:
        connection.close()

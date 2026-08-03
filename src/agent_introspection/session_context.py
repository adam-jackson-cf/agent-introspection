"""Immutable session-context ingestion and deterministic trace correlation."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from agent_introspection.identities import ProjectIdentity
from agent_introspection.telemetry import DerivedEvent

_EVENT_ID = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
Producer = Literal["claude-code", "codex-cli", "codex-app-server", "omp"]
EventType = Literal["session_start", "workspace_changed", "session_end"]
_OPEN_INTERVAL_QUERY = (
    "SELECT event_id FROM session_context_intervals "
    "WHERE producer = ? AND session_id = ? AND ended_at IS NULL"
)
_INSERT_EVENT = (
    "INSERT INTO session_context_events("
    "event_id, producer, session_id, event_type, occurred_at, project_id, "
    "project_name, project_root, project_kind"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_INSERT_INTERVAL = (
    "INSERT INTO session_context_intervals("
    "event_id, producer, session_id, started_at, project_id, project_name, "
    "project_root, project_kind"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
_CLOSE_INTERVAL = (
    "UPDATE session_context_intervals SET ended_at = ?, end_event_id = ? WHERE event_id = ?"
)
_SELECT_EVENT_CANONICAL_FIELDS = (
    "SELECT producer, session_id, event_type, occurred_at, project_id, "
    "project_name, project_root, project_kind "
    "FROM session_context_events WHERE event_id = ?"
)


def _canonical_event_fields(event: SessionContextEvent) -> tuple[str, ...]:
    project_name = event.project.display_name
    if project_name is None:
        raise SessionContextError("agent.project.name is required")
    return (
        event.producer,
        event.session_id,
        event.event_type,
        event.occurred_at.isoformat(),
        event.project.identity,
        project_name,
        event.project.root.as_posix(),
        event.project.kind,
    )


class SessionContextError(ValueError):
    """A context record cannot safely establish project attribution."""


@dataclass(frozen=True, slots=True)
class SessionContextEvent:
    event_id: str
    producer: Producer
    session_id: str
    event_type: EventType
    occurred_at: datetime
    project: ProjectIdentity


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise SessionContextError("occurred_at must be RFC3339 text")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SessionContextError("occurred_at must be RFC3339 text") from exc
    if parsed.tzinfo is None:
        raise SessionContextError("occurred_at must include a timezone")
    return parsed.astimezone(UTC)


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise SessionContextError(f"{name} must be nonempty text")
    return value


def parse_event(value: object) -> SessionContextEvent:
    """Validate the exact immutable event contract without accepting aliases."""
    if not isinstance(value, dict) or set(value) != {
        "event_id",
        "producer",
        "session_id",
        "event_type",
        "occurred_at",
        "agent",
    }:
        raise SessionContextError("context record must contain exactly the canonical fields")
    event_id = _text(value["event_id"], "event_id")
    if _EVENT_ID.fullmatch(event_id) is None:
        raise SessionContextError("event_id must be 64 lowercase hexadecimal characters")
    producer = _text(value["producer"], "producer")
    if producer not in ("claude-code", "codex-cli", "codex-app-server", "omp"):
        raise SessionContextError("producer is unsupported")
    event_type = _text(value["event_type"], "event_type")
    if event_type not in ("session_start", "workspace_changed", "session_end"):
        raise SessionContextError("event_type is unsupported")
    producer = cast(Producer, producer)
    agent = value["agent"]
    if not isinstance(agent, dict) or set(agent) != {"project"}:
        raise SessionContextError("agent must contain exactly project")
    event_type = cast(EventType, event_type)
    project = agent["project"]
    if not isinstance(project, dict) or set(project) != {"id", "name", "root", "kind"}:
        raise SessionContextError("agent.project must contain the complete canonical tuple")
    project_id = _text(project["id"], "agent.project.id")
    if _EVENT_ID.fullmatch(project_id) is None:
        raise SessionContextError("agent.project.id must be 64 lowercase hexadecimal characters")
    root = _text(project["root"], "agent.project.root")
    root_path = Path(root)
    if not root_path.is_absolute() or root_path.as_posix() != root:
        raise SessionContextError("agent.project.root must be a normalized absolute path")
    kind = _text(project["kind"], "agent.project.kind")
    if kind not in ("git", "non_git"):
        raise SessionContextError("agent.project.kind is unsupported")
    return SessionContextEvent(
        event_id,
        producer,
        _text(value["session_id"], "session_id"),
        event_type,
        _timestamp(value["occurred_at"]),
        ProjectIdentity(kind, root_path, project_id, _text(project["name"], "agent.project.name")),
    )


def inbox_path(database_path: Path) -> Path:
    return Path.home() / ".local/share/agent-introspection/session-context-inbox"


def spool_event(event: SessionContextEvent, *, directory: Path) -> Path:
    """Atomically publish an immutable record for the scanner to consume."""
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{event.event_id}.json"
    payload = json.dumps(event_payload(event), sort_keys=True, separators=(",", ":")) + "\n"
    if destination.exists():
        if destination.read_text() != payload:
            raise SessionContextError("event_id conflicts with immutable inbox content")
        return destination
    descriptor, temporary = tempfile.mkstemp(prefix=f".{event.event_id}.", dir=directory)
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return destination


def event_payload(event: SessionContextEvent) -> dict[str, object]:
    return {
        "event_id": event.event_id,
        "producer": event.producer,
        "session_id": event.session_id,
        "event_type": event.event_type,
        "occurred_at": event.occurred_at.isoformat(),
        "agent": {
            "project": {
                "id": event.project.identity,
                "name": event.project.display_name,
                "root": event.project.root.as_posix(),
                "kind": event.project.kind,
            }
        },
    }


def drain_inbox(connection: sqlite3.Connection, *, directory: Path) -> tuple[DerivedEvent, ...]:
    """Persist valid records; malformed and conflicting files remain unresolved in inbox."""
    if not directory.exists():
        return ()
    events: list[DerivedEvent] = []
    pending: list[tuple[Path, SessionContextEvent]] = []
    for path in directory.glob("*.json"):
        try:
            pending.append((path, parse_event(json.loads(path.read_text()))))
        except (OSError, json.JSONDecodeError, SessionContextError):
            continue
    for path, event in sorted(
        pending, key=lambda item: (item[1].occurred_at, item[1].event_id, item[0].name)
    ):
        existing = connection.execute(_SELECT_EVENT_CANONICAL_FIELDS, (event.event_id,)).fetchone()
        if existing is not None:
            if tuple(existing) == _canonical_event_fields(event):
                path.unlink()
            continue
        open_row = connection.execute(
            _OPEN_INTERVAL_QUERY,
            (event.producer, event.session_id),
        ).fetchone()
        if event.event_type == "session_start":
            if open_row is not None:
                continue
            connection.execute(
                _INSERT_EVENT,
                (
                    event.event_id,
                    event.producer,
                    event.session_id,
                    event.event_type,
                    event.occurred_at.isoformat(),
                    event.project.identity,
                    event.project.display_name,
                    event.project.root.as_posix(),
                    event.project.kind,
                ),
            )
            connection.execute(
                _INSERT_INTERVAL,
                (
                    event.event_id,
                    event.producer,
                    event.session_id,
                    event.occurred_at.isoformat(),
                    event.project.identity,
                    event.project.display_name,
                    event.project.root.as_posix(),
                    event.project.kind,
                ),
            )
        elif event.event_type == "workspace_changed":
            if open_row is None:
                continue
            connection.execute("SAVEPOINT workspace_changed_transition")
            try:
                connection.execute(
                    _INSERT_EVENT,
                    (
                        event.event_id,
                        event.producer,
                        event.session_id,
                        event.event_type,
                        event.occurred_at.isoformat(),
                        event.project.identity,
                        event.project.display_name,
                        event.project.root.as_posix(),
                        event.project.kind,
                    ),
                )
                connection.execute(
                    _CLOSE_INTERVAL,
                    (event.occurred_at.isoformat(), event.event_id, open_row[0]),
                )
                connection.execute(
                    _INSERT_INTERVAL,
                    (
                        event.event_id,
                        event.producer,
                        event.session_id,
                        event.occurred_at.isoformat(),
                        event.project.identity,
                        event.project.display_name,
                        event.project.root.as_posix(),
                        event.project.kind,
                    ),
                )
            except sqlite3.Error:
                connection.execute("ROLLBACK TO workspace_changed_transition")
                connection.execute("RELEASE workspace_changed_transition")
                continue
            connection.execute("RELEASE workspace_changed_transition")
        else:
            if open_row is None:
                continue
            connection.execute(
                _INSERT_EVENT,
                (
                    event.event_id,
                    event.producer,
                    event.session_id,
                    event.event_type,
                    event.occurred_at.isoformat(),
                    event.project.identity,
                    event.project.display_name,
                    event.project.root.as_posix(),
                    event.project.kind,
                ),
            )
            connection.execute(
                _CLOSE_INTERVAL,
                (event.occurred_at.isoformat(), event.event_id, open_row[0]),
            )
        project_name = event.project.display_name
        if project_name is None:
            raise SessionContextError("agent.project.name is required")
        path.unlink()
        events.append(
            DerivedEvent(
                scope="session-context",
                entity_id=event.event_id,
                entity_version=1,
                event_sequence=1,
                event_name="introspection.session_context.accepted",
                attributes={
                    "producer": event.producer,
                    "session.id": event.session_id,
                    "event.type": event.event_type,
                    "agent.project.id": event.project.identity,
                    "agent.project.name": project_name,
                },
                timestamp_ns=int(event.occurred_at.timestamp() * 1_000_000_000),
            )
        )
    return tuple(events)


def correlated_project(
    connection: sqlite3.Connection,
    *,
    producer: str | None,
    session_id: str | None,
    started_at: datetime,
    ended_at: datetime,
) -> ProjectIdentity | None:
    if producer not in ("claude-code", "codex-cli", "codex-app-server", "omp") or not session_id:
        return None
    rows = connection.execute(
        (
            "SELECT project_id, project_name, project_root, project_kind "
            "FROM session_context_intervals "
            "WHERE producer = ? AND session_id = ? AND started_at <= ? "
            "AND (ended_at IS NULL OR ended_at > ?)"
        ),
        (producer, session_id, ended_at.isoformat(), started_at.isoformat()),
    ).fetchall()
    if len(rows) != 1:
        return None
    row = rows[0]
    return ProjectIdentity(str(row[3]), Path(str(row[2])), str(row[0]), str(row[1]))

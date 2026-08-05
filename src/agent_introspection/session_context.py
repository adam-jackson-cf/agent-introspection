"""Immutable session-context ingestion and deterministic trace correlation."""

from __future__ import annotations

import hashlib
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
_INSERT_REJECTION = (
    "INSERT OR IGNORE INTO canonical_rejections("
    "id, producer, producer_surface, correlation_id, lifecycle_event, occurred_at, "
    "reason_code, source_adapter, created_at"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_INSERT_REJECTION_WITHOUT_CORRELATION = (
    "INSERT OR IGNORE INTO canonical_rejections("
    "id, producer, producer_surface, correlation_id, lifecycle_event, occurred_at, "
    "reason_code, source_adapter, created_at"
    ") VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?)"
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
    if kind != "git":
        raise SessionContextError("agent.project.kind must be git")
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


def _event_parameters(event: SessionContextEvent) -> tuple[str, ...]:
    return (event.event_id, *_canonical_event_fields(event))


def _rejection_details(value: object, reason_code: str) -> tuple[str, str, str | None, str, str]:
    """Extract only bounded canonical rejection fields from a rejected record."""
    now = datetime.now(UTC).isoformat()
    if not isinstance(value, dict):
        raise SessionContextError("rejection requires a canonical lifecycle envelope")
    producer = value.get("producer")
    lifecycle_event = value.get("event_type")
    if producer not in ("claude-code", "codex-cli", "codex-app-server", "omp"):
        raise SessionContextError("rejection requires a canonical producer")
    if lifecycle_event not in ("session_start", "workspace_changed", "session_end"):
        raise SessionContextError("rejection requires a canonical lifecycle event")
    correlation_id = value.get("session_id")
    if not isinstance(correlation_id, str):
        correlation_id = None
    else:
        correlation_id = correlation_id.strip()
        if not correlation_id:
            correlation_id = None
    occurred_at = value.get("occurred_at")
    if not isinstance(occurred_at, str):
        occurred_at = now
    return producer, "session-context-inbox", correlation_id, lifecycle_event, occurred_at


def _quarantine_path(directory: Path, content: bytes) -> Path:
    quarantine = directory / "quarantine"
    quarantine.mkdir(parents=True, exist_ok=True)
    return quarantine / f"{hashlib.sha256(content).hexdigest()}.json"


def _quarantine(path: Path, content: bytes) -> None:
    os.replace(path, _quarantine_path(path.parent, content))


def _reject(
    connection: sqlite3.Connection,
    *,
    path: Path,
    content: bytes,
    value: object,
    reason_code: str,
) -> None:
    """Durably record a bounded rejection and atomically remove it from the inbox."""
    try:
        producer, surface, correlation_id, lifecycle_event, occurred_at = _rejection_details(
            value, reason_code
        )
    except SessionContextError:
        _quarantine(path, content)
        return
    rejection_id = hashlib.sha256(
        b"\0".join(
            (
                content,
                reason_code.encode(),
                producer.encode(),
                lifecycle_event.encode(),
                occurred_at.encode(),
            )
        )
    ).hexdigest()
    destination = _quarantine_path(path.parent, content)
    with connection:
        os.replace(path, destination)
        if correlation_id is None:
            connection.execute(
                _INSERT_REJECTION_WITHOUT_CORRELATION,
                (
                    rejection_id,
                    producer,
                    surface,
                    lifecycle_event,
                    occurred_at,
                    reason_code,
                    "session-context",
                    datetime.now(UTC).isoformat(),
                ),
            )
        else:
            connection.execute(
                _INSERT_REJECTION,
                (
                    rejection_id,
                    producer,
                    surface,
                    correlation_id,
                    lifecycle_event,
                    occurred_at,
                    reason_code,
                    "session-context",
                    datetime.now(UTC).isoformat(),
                ),
            )


def _parse_rejection_reason(error: SessionContextError) -> str:
    message = str(error)
    if "occurred_at" in message:
        return "invalid_timestamp"
    if "kind" in message:
        return "non_git_workspace"
    if "root" in message:
        return "invalid_workspace"
    return "invalid_workspace"


def _persist_event(connection: sqlite3.Connection, event: SessionContextEvent) -> str | None:
    """Persist one ordered lifecycle event, returning its rejection code when invalid."""
    existing = connection.execute(_SELECT_EVENT_CANONICAL_FIELDS, (event.event_id,)).fetchone()
    if existing is not None:
        return (
            "idempotent"
            if tuple(existing) == _canonical_event_fields(event)
            else "duplicate_conflict"
        )
    open_row = connection.execute(
        "SELECT event_id, started_at FROM session_context_intervals "
        "WHERE producer = ? AND session_id = ? AND ended_at IS NULL",
        (event.producer, event.session_id),
    ).fetchone()
    if event.event_type == "session_start":
        if open_row is not None:
            return "invalid_transition"
        connection.execute(_INSERT_EVENT, _event_parameters(event))
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
        return None
    if open_row is None:
        return "invalid_transition"
    if event.occurred_at.isoformat() < str(open_row[1]):
        return "out_of_order_event"
    connection.execute(_INSERT_EVENT, _event_parameters(event))
    updated = connection.execute(
        _CLOSE_INTERVAL + " AND ended_at IS NULL AND started_at <= ?",
        (
            event.occurred_at.isoformat(),
            event.event_id,
            open_row[0],
            event.occurred_at.isoformat(),
        ),
    )
    if updated.rowcount != 1:
        raise sqlite3.IntegrityError("open interval ownership changed")
    if event.event_type == "workspace_changed":
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
    return None


def drain_inbox(connection: sqlite3.Connection, *, directory: Path) -> tuple[DerivedEvent, ...]:
    """Transactionally replay ordered lifecycle records and quarantine every rejection."""
    if not directory.exists():
        return ()
    pending: list[tuple[Path, bytes, object, SessionContextEvent]] = []
    for path in directory.glob("*.json"):
        try:
            content = path.read_bytes()
        except OSError:
            continue
        try:
            value = json.loads(content)
        except json.JSONDecodeError:
            _quarantine(path, content)
            continue
        try:
            event = parse_event(value)
        except SessionContextError as error:
            _reject(
                connection,
                path=path,
                content=content,
                value=value,
                reason_code=_parse_rejection_reason(error),
            )
            continue
        pending.append((path, content, value, event))
    events: list[DerivedEvent] = []
    for path, content, value, event in sorted(
        pending, key=lambda item: (item[3].occurred_at, item[3].event_id, item[0].name)
    ):
        try:
            with connection:
                reason_code = _persist_event(connection, event)
                if reason_code in (None, "idempotent"):
                    path.unlink()
                else:
                    _reject(
                        connection,
                        path=path,
                        content=content,
                        value=value,
                        reason_code=reason_code,
                    )
        except sqlite3.Error:
            _reject(
                connection,
                path=path,
                content=content,
                value=value,
                reason_code="invalid_transition",
            )
            continue
        if reason_code is not None:
            continue
        attributes: dict[str, str | int | float | bool] = {
            "producer": event.producer,
            "session.id": event.session_id,
            "event.type": event.event_type,
            "agent.project.id": event.project.identity,
        }
        if event.project.display_name is not None:
            attributes["agent.project.name"] = event.project.display_name
        events.append(
            DerivedEvent(
                scope="session-context",
                entity_id=event.event_id,
                entity_version=1,
                event_sequence=1,
                event_name="introspection.session_context.accepted",
                attributes=attributes,
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
            "AND (ended_at IS NULL OR ended_at >= ?)"
        ),
        (producer, session_id, started_at.isoformat(), ended_at.isoformat()),
    ).fetchall()
    if len(rows) != 1:
        return None
    row = rows[0]
    return ProjectIdentity(str(row[3]), Path(str(row[2])), str(row[0]), str(row[1]))

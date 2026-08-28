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
from agent_introspection.telemetry import DerivedEvent, enqueue_event

_EVENT_ID = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
Producer = Literal["claude-code", "codex-cli", "codex-app-server", "omp"]
EventType = Literal["session_start", "workspace_changed", "session_end", "session_context"]
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
_OPEN_RAW_RECONCILIATION = (
    "INSERT INTO source_session_reconciliation_pending("
    "producer, session_id, context_event_id, created_at, completed_at"
    ") VALUES (?, ?, ?, ?, NULL) "
    "ON CONFLICT(producer, session_id) DO UPDATE SET "
    "context_event_id = excluded.context_event_id, created_at = excluded.created_at, "
    "completed_at = NULL"
)

_INSERT_SUPERSESSION = (
    "INSERT INTO session_context_event_supersessions("
    "original_event_id, replacement_event_id, created_at"
    ") VALUES (?, ?, ?) ON CONFLICT(original_event_id) DO NOTHING"
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

_REJECTION_REASONS = frozenset(
    {
        "missing_correlation_id",
        "conflicting_correlation_id",
        "missing_workspace",
        "invalid_workspace",
        "non_git_workspace",
        "git_resolution_failed",
        "invalid_timestamp",
        "invalid_transition",
        "duplicate_conflict",
        "out_of_order_event",
    }
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


def _parse_producer(value: object) -> Producer:
    producer = _text(value, "producer")
    if producer not in ("claude-code", "codex-cli", "codex-app-server", "omp"):
        raise SessionContextError("producer is unsupported")
    return cast(Producer, producer)


def _parse_event_type(value: object, *, producer: Producer) -> EventType:
    event_type = _text(value, "event_type")
    if event_type not in ("session_start", "workspace_changed", "session_end", "session_context"):
        raise SessionContextError("event_type is unsupported")
    if event_type == "session_context" and producer != "codex-cli":
        raise SessionContextError("session_context is only supported for producer codex-cli")
    return cast(EventType, event_type)


def _parse_project(value: object) -> ProjectIdentity:
    if not isinstance(value, dict) or set(value) != {"id", "name", "root", "kind"}:
        raise SessionContextError("agent.project must contain the complete canonical tuple")
    project_id = _text(value["id"], "agent.project.id")
    if _EVENT_ID.fullmatch(project_id) is None:
        raise SessionContextError("agent.project.id must be 64 lowercase hexadecimal characters")
    root = _text(value["root"], "agent.project.root")
    root_path = Path(root)
    if not root_path.is_absolute() or root_path.as_posix() != root:
        raise SessionContextError("agent.project.root must be a normalized absolute path")
    kind = _text(value["kind"], "agent.project.kind")
    if kind != "git":
        raise SessionContextError("agent.project.kind must be git")
    return ProjectIdentity(kind, root_path, project_id, _text(value["name"], "agent.project.name"))


def _parse_agent_project(value: object) -> ProjectIdentity:
    if not isinstance(value, dict) or set(value) != {"project"}:
        raise SessionContextError("agent must contain exactly project")
    return _parse_project(value["project"])


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
    producer = _parse_producer(value["producer"])
    return SessionContextEvent(
        event_id,
        producer,
        _text(value["session_id"], "session_id"),
        _parse_event_type(value["event_type"], producer=producer),
        _timestamp(value["occurred_at"]),
        _parse_agent_project(value["agent"]),
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
    if lifecycle_event not in (
        "session_start",
        "workspace_changed",
        "session_end",
        "session_context",
    ) or (lifecycle_event == "session_context" and producer != "codex-cli"):
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


def _rejection_envelope_details(
    value: object,
) -> tuple[str, str, str, str, str, str, str, str] | None:
    if not isinstance(value, dict) or set(value) != {
        "rejection_id",
        "producer",
        "producer_surface",
        "correlation_id",
        "lifecycle_event",
        "occurred_at",
        "reason_code",
        "source_adapter",
    }:
        return None
    rejection_id = _text(value["rejection_id"], "rejection_id")
    producer = _text(value["producer"], "producer")
    surface = _text(value["producer_surface"], "producer_surface")
    correlation_id = _text(value["correlation_id"], "correlation_id")
    lifecycle_event = _text(value["lifecycle_event"], "lifecycle_event")
    reason_code = _text(value["reason_code"], "reason_code")
    source_adapter = _text(value["source_adapter"], "source_adapter")
    if (
        _EVENT_ID.fullmatch(rejection_id) is None
        or producer not in ("claude-code", "codex-cli", "codex-app-server", "omp")
        or lifecycle_event
        not in ("session_start", "workspace_changed", "session_end", "session_context")
        or (lifecycle_event == "session_context" and producer != "codex-cli")
        or reason_code not in _REJECTION_REASONS
    ):
        raise SessionContextError("rejection envelope is not canonical")
    return (
        rejection_id,
        producer,
        surface,
        correlation_id,
        lifecycle_event,
        _timestamp(value["occurred_at"]).isoformat(),
        reason_code,
        source_adapter,
    )


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


def _open_raw_reconciliation(connection: sqlite3.Connection, event: SessionContextEvent) -> None:
    """Durably request exact raw-session reconciliation for accepted context."""
    connection.execute(
        _OPEN_RAW_RECONCILIATION,
        (event.producer, event.session_id, event.event_id, datetime.now(UTC).isoformat()),
    )


def _existing_event_result(
    connection: sqlite3.Connection, event: SessionContextEvent
) -> str | None:
    existing = connection.execute(_SELECT_EVENT_CANONICAL_FIELDS, (event.event_id,)).fetchone()
    if existing is None:
        return None
    return (
        "idempotent" if tuple(existing) == _canonical_event_fields(event) else "duplicate_conflict"
    )


def _is_out_of_order(
    connection: sqlite3.Connection, event: SessionContextEvent, *, clock_skew_seconds: int
) -> bool:
    latest = connection.execute(
        "SELECT latest_occurred_at FROM session_context_replay_state "
        "WHERE producer = ? AND session_id = ?",
        (event.producer, event.session_id),
    ).fetchone()
    if latest is None:
        return False
    latest_at = datetime.fromisoformat(str(latest[0])).astimezone(UTC)
    return (latest_at - event.occurred_at).total_seconds() > clock_skew_seconds


def _session_lifecycle_events(
    connection: sqlite3.Connection, event: SessionContextEvent
) -> list[SessionContextEvent]:
    rows = connection.execute(
        "SELECT event_id, producer, session_id, event_type, occurred_at, project_id, "
        "project_name, project_root, project_kind FROM session_context_events "
        "WHERE producer = ? AND session_id = ? ORDER BY occurred_at, event_id",
        (event.producer, event.session_id),
    ).fetchall()
    ordered = [event]
    ordered.extend(
        SessionContextEvent(
            event_id=str(row[0]),
            producer=cast(Producer, row[1]),
            session_id=str(row[2]),
            event_type=cast(EventType, row[3]),
            occurred_at=datetime.fromisoformat(str(row[4])).astimezone(UTC),
            project=ProjectIdentity(str(row[8]), Path(str(row[7])), str(row[5]), str(row[6])),
        )
        for row in rows
    )
    return sorted(ordered, key=lambda item: (item.occurred_at, item.event_id))


def _lifecycle_intervals(
    ordered: list[SessionContextEvent],
) -> list[tuple[SessionContextEvent, SessionContextEvent | None]] | None:
    open_event: SessionContextEvent | None = None
    intervals: list[tuple[SessionContextEvent, SessionContextEvent | None]] = []
    for item in ordered:
        if item.event_type == "session_start":
            if open_event is not None:
                return None
            open_event = item
        elif open_event is None:
            return None
        else:
            intervals.append((open_event, item))
            open_event = item if item.event_type == "workspace_changed" else None
    if open_event is not None:
        intervals.append((open_event, None))
    return intervals


def _store_replay(
    connection: sqlite3.Connection,
    event: SessionContextEvent,
    intervals: list[tuple[SessionContextEvent, SessionContextEvent | None]],
) -> None:
    connection.execute(_INSERT_EVENT, _event_parameters(event))
    connection.execute(
        "INSERT INTO session_context_replay_state(producer, session_id, latest_occurred_at) "
        "VALUES (?, ?, ?) ON CONFLICT(producer, session_id) DO UPDATE SET "
        "latest_occurred_at = MAX(latest_occurred_at, excluded.latest_occurred_at)",
        (event.producer, event.session_id, event.occurred_at.isoformat()),
    )
    connection.execute(
        "INSERT INTO session_context_replay_mutations(producer, session_id) VALUES (?, ?)",
        (event.producer, event.session_id),
    )
    connection.execute(
        "DELETE FROM session_context_intervals WHERE producer = ? AND session_id = ?",
        (event.producer, event.session_id),
    )
    for start, end in intervals:
        connection.execute(
            _INSERT_INTERVAL,
            (
                start.event_id,
                start.producer,
                start.session_id,
                start.occurred_at.isoformat(),
                start.project.identity,
                start.project.display_name,
                start.project.root.as_posix(),
                start.project.kind,
            ),
        )
        if end is not None:
            connection.execute(
                _CLOSE_INTERVAL,
                (end.occurred_at.isoformat(), end.event_id, start.event_id),
            )
    connection.execute(
        "DELETE FROM session_context_replay_mutations WHERE producer = ? AND session_id = ?",
        (event.producer, event.session_id),
    )
    _open_raw_reconciliation(connection, event)


def _replay_intervals(
    connection: sqlite3.Connection, event: SessionContextEvent, *, clock_skew_seconds: int
) -> str | None:
    """Insert a non-temporal context or rebuild an ordered lifecycle interval projection."""
    existing_result = _existing_event_result(connection, event)
    if existing_result is not None:
        return existing_result
    if event.event_type == "session_context":
        connection.execute(_INSERT_EVENT, _event_parameters(event))
        _open_raw_reconciliation(connection, event)
        return None
    if _is_out_of_order(connection, event, clock_skew_seconds=clock_skew_seconds):
        return "out_of_order_event"
    intervals = _lifecycle_intervals(_session_lifecycle_events(connection, event))
    if intervals is None:
        return "invalid_transition"
    _store_replay(connection, event, intervals)
    return None


def supersede_context(
    connection: sqlite3.Connection, *, original_event_id: str, replacement_event_id: str
) -> DerivedEvent:
    """Append one immutable correction that withdraws an accepted context identity."""
    if (
        _EVENT_ID.fullmatch(original_event_id) is None
        or _EVENT_ID.fullmatch(replacement_event_id) is None
        or original_event_id == replacement_event_id
    ):
        raise SessionContextError("supersession requires distinct canonical event IDs")
    original = connection.execute(
        "SELECT producer, session_id FROM session_context_events WHERE event_id = ?",
        (original_event_id,),
    ).fetchone()
    replacement = connection.execute(
        "SELECT producer, session_id FROM session_context_events WHERE event_id = ?",
        (replacement_event_id,),
    ).fetchone()
    if original is None or replacement is None:
        raise SessionContextError("supersession requires accepted context events")
    if tuple(original) != tuple(replacement):
        raise SessionContextError("supersession requires the same producer and session")
    existing = connection.execute(
        "SELECT replacement_event_id FROM session_context_event_supersessions "
        "WHERE original_event_id = ?",
        (original_event_id,),
    ).fetchone()
    if existing is not None and str(existing[0]) != replacement_event_id:
        raise SessionContextError("context event already has an immutable supersession")
    connection.execute(
        _INSERT_SUPERSESSION,
        (original_event_id, replacement_event_id, datetime.now(UTC).isoformat()),
    )
    connection.execute(
        _OPEN_RAW_RECONCILIATION,
        (
            str(replacement[0]),
            str(replacement[1]),
            replacement_event_id,
            datetime.now(UTC).isoformat(),
        ),
    )
    timestamp_ns = int(datetime.now(UTC).timestamp() * 1_000_000_000)
    event = DerivedEvent(
        scope="session-context-supersession",
        entity_id=original_event_id,
        entity_version=1,
        event_sequence=1,
        event_name="introspection.session_context.superseded",
        attributes={"replacement.event_id": replacement_event_id},
        timestamp_ns=timestamp_ns,
    )
    outbox = connection.execute(
        "SELECT payload_json FROM otlp_outbox WHERE event_id = ?", (event.event_id,)
    ).fetchone()
    if outbox is not None:
        timestamp_ns = int(json.loads(str(outbox[0]))["timestamp_ns"])
        event = DerivedEvent(
            scope=event.scope,
            entity_id=event.entity_id,
            entity_version=event.entity_version,
            event_sequence=event.event_sequence,
            event_name=event.event_name,
            attributes=event.attributes,
            timestamp_ns=timestamp_ns,
        )
    enqueue_event(connection, event)
    return event


@dataclass(frozen=True, slots=True)
class _InboxEvent:
    path: Path
    content: bytes
    value: object
    event: SessionContextEvent


def _load_inbox_event(connection: sqlite3.Connection, path: Path) -> _InboxEvent | None:
    try:
        content = path.read_bytes()
    except OSError:
        return None
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        _quarantine(path, content)
        return None
    try:
        rejection = _rejection_envelope_details(value)
    except SessionContextError:
        _quarantine(path, content)
        return None
    if rejection is not None:
        with connection:
            os.replace(path, _quarantine_path(path.parent, content))
            connection.execute(_INSERT_REJECTION, (*rejection, datetime.now(UTC).isoformat()))
        return None
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
        return None
    return _InboxEvent(path, content, value, event)


def _accepted_context_event(event: SessionContextEvent) -> DerivedEvent:
    attributes: dict[str, str | int | float | bool] = {
        "producer": event.producer,
        "session.id": event.session_id,
        "event.type": event.event_type,
        "agent.project.id": event.project.identity,
    }
    if event.project.display_name is not None:
        attributes["agent.project.name"] = event.project.display_name
    return DerivedEvent(
        scope="session-context",
        entity_id=event.event_id,
        entity_version=2,
        event_sequence=1,
        event_name="introspection.session_context.accepted",
        attributes=attributes,
        timestamp_ns=int(event.occurred_at.timestamp() * 1_000_000_000),
    )


def _ingest_inbox_event(
    connection: sqlite3.Connection, inbox_event: _InboxEvent, *, clock_skew_seconds: int
) -> DerivedEvent | None:
    try:
        with connection:
            reason_code = _replay_intervals(
                connection, inbox_event.event, clock_skew_seconds=clock_skew_seconds
            )
            if reason_code in (None, "idempotent"):
                inbox_event.path.unlink()
            else:
                _reject(
                    connection,
                    path=inbox_event.path,
                    content=inbox_event.content,
                    value=inbox_event.value,
                    reason_code=reason_code,
                )
    except sqlite3.Error:
        _reject(
            connection,
            path=inbox_event.path,
            content=inbox_event.content,
            value=inbox_event.value,
            reason_code="invalid_transition",
        )
        return None
    if reason_code is not None:
        return None
    return _accepted_context_event(inbox_event.event)


def drain_inbox(
    connection: sqlite3.Connection, *, directory: Path, clock_skew_seconds: int = 300
) -> tuple[DerivedEvent, ...]:
    """Transactionally ingest canonical context and quarantine every rejection."""
    if not directory.exists():
        return ()
    pending = [
        inbox_event
        for path in directory.glob("*.json")
        if (inbox_event := _load_inbox_event(connection, path)) is not None
    ]
    ordered = sorted(
        pending,
        key=lambda item: (item.event.occurred_at, item.event.event_id, item.path.name),
    )
    return tuple(
        derived_event
        for inbox_event in ordered
        if (
            derived_event := _ingest_inbox_event(
                connection, inbox_event, clock_skew_seconds=clock_skew_seconds
            )
        )
        is not None
    )


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

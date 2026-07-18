"""Duplicate-tolerant OTLP log outbox."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.logs.v1.logs_pb2 import LogRecord, ResourceLogs, ScopeLogs
from opentelemetry.proto.resource.v1.resource_pb2 import Resource

from agent_introspection.source import SourceError

SERVICE_NAME = "agent-introspection"
OBSERVATION_EVENT_NAME = "introspection.observation.detected"
OPERATIONAL_SCOPE = "operational"
REVIEW_SCOPE = "review"


class EventQueryClient(Protocol):
    """The read-only query contract required for remote event verification."""

    def query(
        self, sql: str, parameters: Mapping[str, str | int]
    ) -> Iterable[Mapping[str, Any]]: ...


@dataclass(frozen=True)
class DerivedEvent:
    scope: str
    entity_id: str
    entity_version: int
    event_sequence: int
    event_name: str
    attributes: dict[str, str | int | float | bool]
    timestamp_ns: int

    @property
    def event_id(self) -> str:
        if not self.scope:
            raise ValueError("event scope is required")
        material = "\x1f".join(
            (
                self.scope,
                self.entity_id,
                str(self.entity_version),
                str(self.event_sequence),
                self.event_name,
            )
        )
        return str(uuid.UUID(hashlib.sha256(material.encode()).hexdigest()[:32]))

    def payload(self) -> dict[str, Any]:
        return {
            "event.id": self.event_id,
            "event.scope": self.scope,
            "entity.id": self.entity_id,
            "entity.version": self.entity_version,
            "event.sequence": self.event_sequence,
            "event.name": self.event_name,
            "timestamp_ns": self.timestamp_ns,
            **self.attributes,
        }


@dataclass(frozen=True)
class ObservationReconciliationPlan:
    """A fully validated, immutable-event recovery plan for failed scan observations."""

    scan_run_ids: tuple[str, ...]
    observation_count: int
    existing_local_observation_events: int
    events: tuple[DerivedEvent, ...]


@dataclass(frozen=True)
class RemoteEventReference:
    """The immutable fields required to confirm one delivered OTLP event."""

    event_id: str
    event_name: str
    timestamp_ns: int


def enqueue_event(connection: sqlite3.Connection, event: DerivedEvent) -> str:
    """Persist an immutable event payload; duplicate events coalesce by ID."""
    enqueue_events(connection, [event])
    return event.event_id


def enqueue_events(connection: sqlite3.Connection, events: list[DerivedEvent]) -> list[str]:
    """Persist a deterministic event batch in one transaction."""
    now = datetime.now(UTC).isoformat()

    def write() -> None:
        connection.executemany(
            """
            INSERT INTO otlp_outbox (
                event_id, payload_json, status, attempt_count, next_attempt_at, created_at
            ) VALUES (?, ?, 'pending', 0, ?, ?)
            ON CONFLICT(event_id) DO NOTHING
            """,
            [
                (
                    event.event_id,
                    json.dumps(event.payload(), sort_keys=True, separators=(",", ":")),
                    now,
                    now,
                )
                for event in events
            ],
        )

    if connection.in_transaction:
        write()
    else:
        with connection:
            write()
    return [event.event_id for event in events]


def _local_observation_event_ids(connection: sqlite3.Connection) -> set[str]:
    """Return observation entities already represented by the immutable local outbox."""

    existing_observation_ids: set[str] = set()
    for row in connection.execute("SELECT payload_json FROM otlp_outbox"):
        payload = json.loads(str(row[0]))
        if payload.get("event.name") == OBSERVATION_EVENT_NAME:
            entity_id = payload.get("entity.id")
            if isinstance(entity_id, str):
                existing_observation_ids.add(entity_id)
    return existing_observation_ids


def _validate_failed_scan_ids(
    connection: sqlite3.Connection, scan_run_ids: Sequence[str]
) -> tuple[str, ...]:
    selected = tuple(scan_run_ids)
    if not selected or any(
        not isinstance(scan_run_id, str) or not scan_run_id for scan_run_id in selected
    ):
        raise ValueError("at least one non-empty failed scan run ID is required")
    if len(set(selected)) != len(selected):
        raise ValueError("failed scan run IDs must be unique")
    placeholders = ",".join("?" for _ in selected)
    rows = connection.execute(
        f"SELECT id, status FROM scan_runs WHERE id IN ({placeholders})", selected
    ).fetchall()
    statuses = {str(row[0]): str(row[1]) for row in rows}
    missing = sorted(set(selected) - set(statuses))
    if missing:
        raise ValueError(f"failed scan run IDs do not exist: {missing!r}")
    non_failed = sorted(
        scan_run_id for scan_run_id, status in statuses.items() if status != "failed"
    )
    if non_failed:
        raise ValueError(f"scan runs are not failed: {non_failed!r}")
    return selected


def plan_observation_reconciliation(
    connection: sqlite3.Connection, *, scan_run_ids: Sequence[str]
) -> ObservationReconciliationPlan:
    """Reconstruct original observation event identities for explicit failed scans."""

    selected_scan_run_ids = _validate_failed_scan_ids(connection, scan_run_ids)
    existing_local_observation_ids = _local_observation_event_ids(connection)
    events: list[DerivedEvent] = []
    observation_count = 0
    for scan_run_id in selected_scan_run_ids:
        rows = connection.execute(
            """
            SELECT o.id, o.detector_id, o.project_identity_id, p.canonical_path,
                   o.fingerprint, o.occurred_at_ns, o.task_identity, o.attributes_json
            FROM observations o
            LEFT JOIN project_identities p ON p.id = o.project_identity_id
            WHERE o.scan_run_id = ?
            """,
            (scan_run_id,),
        ).fetchall()
        candidates: list[tuple[tuple[str, str, tuple[str, ...]], DerivedEvent]] = []
        for row in rows:
            observation_id = str(row[0])
            detector_id = str(row[1])
            project_id = str(row[2]) if row[2] is not None else None
            project_path = Path(str(row[3])) if row[3] is not None else None
            fingerprint = str(row[4])
            occurred_at_ns = int(row[5])
            task_identity = str(row[6]) if row[6] is not None else None
            attributes = json.loads(str(row[7]))
            event_ids = attributes.get("event_ids") if isinstance(attributes, dict) else None
            if (
                not isinstance(event_ids, list)
                or not event_ids
                or any(not isinstance(event_id, str) or not event_id for event_id in event_ids)
            ):
                raise ValueError(f"observation {observation_id!r} has no non-empty event_ids array")
            event_id_tuple = tuple(event_ids)
            project_sort_key = project_id
            if project_sort_key is None:
                if task_identity is None:
                    raise ValueError(
                        f"unresolved observation {observation_id!r} has no task identity"
                    )
                project_sort_key = f"unresolved:{task_identity}"
            candidates.append(
                (
                    (detector_id, project_sort_key, event_id_tuple),
                    DerivedEvent(
                        scope=OPERATIONAL_SCOPE,
                        entity_id=observation_id,
                        entity_version=1,
                        event_sequence=0,
                        event_name=OBSERVATION_EVENT_NAME,
                        attributes={
                            "detector.id": detector_id,
                            "project.id": project_id or "unresolved",
                            "project.name": project_path.name if project_path else "unresolved",
                            "finding.id": fingerprint,
                        },
                        timestamp_ns=occurred_at_ns,
                    ),
                )
            )
        for ordinal, (_sort_key, event) in enumerate(
            sorted(candidates, key=lambda item: item[0]), 1
        ):
            recovered = DerivedEvent(
                scope=OPERATIONAL_SCOPE,
                entity_id=event.entity_id,
                entity_version=event.entity_version,
                event_sequence=ordinal,
                event_name=event.event_name,
                attributes=event.attributes,
                timestamp_ns=event.timestamp_ns,
            )
            if recovered.entity_id not in existing_local_observation_ids:
                events.append(recovered)
        observation_count += len(candidates)
    return ObservationReconciliationPlan(
        scan_run_ids=selected_scan_run_ids,
        observation_count=observation_count,
        existing_local_observation_events=observation_count - len(events),
        events=tuple(events),
    )


def remote_event_ids(
    client: EventQueryClient, events: Sequence[DerivedEvent | RemoteEventReference]
) -> set[str]:
    """Check SigNoz for exact immutable event IDs before a control-plane transition."""

    references = tuple(
        RemoteEventReference(event.event_id, event.event_name, event.timestamp_ns)
        if isinstance(event, DerivedEvent)
        else event
        for event in events
    )
    present: set[str] = set()
    by_name: dict[str, list[RemoteEventReference]] = {}
    for reference in references:
        if not reference.event_id or not reference.event_name or reference.timestamp_ns < 0:
            raise ValueError(
                "remote event references require an ID, name, and non-negative timestamp"
            )
        by_name.setdefault(reference.event_name, []).append(reference)
    for event_name, named_references in sorted(by_name.items()):
        for offset in range(0, len(named_references), 250):
            batch = named_references[offset : offset + 250]
            event_ids = {event.event_id for event in batch}
            placeholders = ", ".join(f"{{event_{index}:String}}" for index in range(len(batch)))
            start_ns = min(event.timestamp_ns for event in batch)
            end_ns = max(event.timestamp_ns for event in batch)
            query = f"""
        SELECT DISTINCT attributes_string['event.id'] AS event_id
        FROM signoz_logs.distributed_logs_v2
        WHERE timestamp BETWEEN {{start_ns:UInt64}} AND {{end_ns:UInt64}}
          AND ts_bucket_start BETWEEN {{start_bucket:UInt64}} AND {{end_bucket:UInt64}}
          AND resource.`service.name`::String = 'agent-introspection'
          AND attributes_string['event.name'] = {{event_name:String}}
          AND attributes_string['event.id'] IN ({placeholders})
        """.strip()
            parameters: dict[str, str | int] = {
                "start_ns": start_ns,
                "end_ns": end_ns,
                "start_bucket": max(0, start_ns // 1_000_000_000 - 1800),
                "end_bucket": end_ns // 1_000_000_000,
                "event_name": event_name,
            }
            parameters.update(
                {f"event_{index}": event.event_id for index, event in enumerate(batch)}
            )
            for row in client.query(query, parameters):
                event_id = row.get("event_id")
                if not isinstance(event_id, str) or event_id not in event_ids:
                    raise SourceError("SigNoz returned an unexpected event ID")
                present.add(event_id)
    return present


def remote_observation_event_ids(
    client: EventQueryClient, events: Sequence[DerivedEvent]
) -> set[str]:
    """Check SigNoz for exact observation event IDs before local replay."""

    if any(event.event_name != OBSERVATION_EVENT_NAME for event in events):
        raise ValueError("observation reconciliation requires observation events")
    return remote_event_ids(client, events)


def enqueue_observation_reconciliation(
    connection: sqlite3.Connection,
    plan: ObservationReconciliationPlan,
    *,
    remote_event_ids: set[str],
) -> dict[str, int]:
    """Persist only the planned observation events absent from both idempotency ledgers."""

    planned_event_ids = {event.event_id for event in plan.events}
    unexpected_remote_ids = remote_event_ids - planned_event_ids
    if unexpected_remote_ids:
        raise ValueError("remote observation event IDs are outside the reconciliation plan")
    queued_events = [event for event in plan.events if event.event_id not in remote_event_ids]
    enqueue_events(connection, queued_events)
    return {
        "observations": plan.observation_count,
        "existing_local_observation_events": plan.existing_local_observation_events,
        "existing_remote_observation_events": len(remote_event_ids),
        "queued_observation_events": len(queued_events),
    }


def _any_value(value: str | int | float | bool) -> AnyValue:
    if isinstance(value, bool):
        return AnyValue(bool_value=value)
    if isinstance(value, int):
        return AnyValue(int_value=value)
    if isinstance(value, float):
        return AnyValue(double_value=value)
    return AnyValue(string_value=value)


def _encode_otlp(payloads: list[dict[str, Any]]) -> bytes:
    records: list[LogRecord] = []
    for payload in payloads:
        timestamp_ns = int(payload.pop("timestamp_ns"))
        attributes = [KeyValue(key=key, value=_any_value(value)) for key, value in payload.items()]
        records.append(
            LogRecord(
                time_unix_nano=timestamp_ns,
                observed_time_unix_nano=time.time_ns(),
                body=AnyValue(string_value=str(payload["event.name"])),
                attributes=attributes,
            )
        )
    request = ExportLogsServiceRequest(
        resource_logs=[
            ResourceLogs(
                resource=Resource(
                    attributes=[
                        KeyValue(key="service.name", value=AnyValue(string_value=SERVICE_NAME))
                    ]
                ),
                scope_logs=[ScopeLogs(log_records=records)],
            )
        ]
    )
    return bytes(request.SerializeToString())


def drain_outbox(
    connection: sqlite3.Connection,
    *,
    endpoint: str = "http://localhost:4318/v1/logs",
    limit: int = 100,
    timeout_seconds: float = 10,
) -> dict[str, int]:
    """Deliver pending events, retaining identical IDs and payloads across retries."""
    now = datetime.now(UTC).isoformat()
    rows = connection.execute(
        """
        SELECT event_id, payload_json, attempt_count
        FROM otlp_outbox
        WHERE status = 'pending' AND next_attempt_at <= ?
        ORDER BY created_at, event_id
        LIMIT ?
        """,
        (now, limit),
    ).fetchall()
    if not rows:
        return {"selected": 0, "delivered": 0, "pending": 0}
    payloads = [json.loads(row[1]) for row in rows]
    request = urllib.request.Request(
        endpoint,
        data=_encode_otlp(payloads),
        headers={"Content-Type": "application/x-protobuf"},
        method="POST",
    )
    delivered = False
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            delivered = 200 <= response.status < 300
    except (urllib.error.URLError, TimeoutError):
        delivered = False
    with connection:
        if delivered:
            connection.executemany(
                "UPDATE otlp_outbox SET status = 'delivered', delivered_at = ? WHERE event_id = ?",
                [(now, row[0]) for row in rows],
            )
        else:
            for event_id, _payload, attempt_count in rows:
                delay = min(3600, 2 ** min(int(attempt_count), 11))
                next_attempt = datetime.fromtimestamp(time.time() + delay, tz=UTC).isoformat()
                connection.execute(
                    """
                    UPDATE otlp_outbox
                    SET attempt_count = attempt_count + 1, next_attempt_at = ?
                    WHERE event_id = ?
                    """,
                    (next_attempt, event_id),
                )
    pending = connection.execute(
        "SELECT COUNT(*) FROM otlp_outbox WHERE status = 'pending'"
    ).fetchone()[0]
    return {
        "selected": len(rows),
        "delivered": len(rows) if delivered else 0,
        "pending": int(pending),
    }

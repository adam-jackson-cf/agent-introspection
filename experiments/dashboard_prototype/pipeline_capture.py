"""Isolated, read-only durable capture for pipeline proof inputs.

The capture database is experiment-owned. It stores only the exact application
rows that existing proof readers may inspect; unavailable owners are omitted so
those readers fail closed instead of accepting a look-alike production schema.
"""

from __future__ import annotations

import hashlib
import json
import socket
import sqlite3
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agent_introspection.config import SchedulerConfig
from agent_introspection.telemetry import DerivedEvent
from experiments.dashboard_prototype.pipeline_live_common import LiveProofRequest


class PipelineCaptureError(ValueError):
    """The requested capture cannot preserve an exact bounded source population."""


_SCAN_COLUMNS = (
    "id",
    "status",
    "started_at",
    "completed_at",
    "source_start_ns",
    "source_end_ns",
    "rows_processed",
    "error_code",
)
_REJECTION_COLUMNS = (
    "id",
    "producer",
    "producer_surface",
    "lifecycle_event",
    "occurred_at",
    "reason_code",
    "source_adapter",
)
_SOURCE_SESSION_COLUMNS = (
    "scan_run_id",
    "source_kind",
    "service_name",
    "source_id",
    "source_timestamp",
    "source_timestamp_ns",
    "context_evidence_id",
)
_CONTEXT_COLUMNS = ("event_id", "producer", "occurred_at")
_EXECUTION_INPUT_COLUMNS = (
    "scan_run_id",
    "monotonic_started",
    "monotonic_finished",
    "logs_json",
    "traces_json",
    "context_events_json",
    "canonical_activities_json",
)

_NATIVE_PIPELINE_SNAPSHOT_COLUMNS = (
    "event_id",
    "payload_json",
)

_NATIVE_PIPELINE_SNAPSHOT_EVENT = "introspection.pipeline.snapshot"


def capture(
    source: sqlite3.Connection,
    destination: Path,
    request: LiveProofRequest,
    scheduler: SchedulerConfig,
) -> Path:
    """Freeze bounded application rows and a separately timed maintenance observation."""
    if destination.exists():
        raise PipelineCaptureError("capture destination already exists")
    if request.start.tzinfo is None or request.end.tzinfo is None:
        raise PipelineCaptureError("capture window must be timezone-aware")
    destination.parent.mkdir(parents=True, exist_ok=True)
    target = sqlite3.connect(destination)
    owns_snapshot = not source.in_transaction
    if owns_snapshot:
        source.execute("BEGIN")
    try:
        _create_schema(target)
        with target:
            target.execute(
                "INSERT INTO capture_metadata VALUES (?, ?, ?, ?, ?)",
                (
                    request.run_id,
                    _utc_now(),
                    request.start.astimezone(UTC).isoformat(),
                    request.end.astimezone(UTC).isoformat(),
                    scheduler.timezone,
                ),
            )
            target.execute(
                "INSERT INTO schedule_policy VALUES (?, ?, ?)",
                (scheduler.interval_seconds, scheduler.lease_seconds, scheduler.timezone),
            )
            _copy_scans(source, target, request)
            _copy_execution_inputs(source, target, request)
            _copy_source_sessions(source, target, request)
            _copy_context_events(source, target, request)
            _copy_rejections(source, target, request)
            _copy_native_pipeline_snapshots(source, target, request)
            _capture_maintenance(target, source)
    finally:
        target.close()
        if owns_snapshot:
            source.rollback()
    return destination


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE capture_metadata (
            run_id TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            bounded_start TEXT NOT NULL,
            bounded_end TEXT NOT NULL,
            scheduler_timezone TEXT NOT NULL
        ) STRICT;
        CREATE TABLE schedule_policy (
            interval_seconds INTEGER NOT NULL,
            lease_seconds INTEGER NOT NULL,
            timezone TEXT NOT NULL
        ) STRICT;
        CREATE TABLE scan_runs (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            source_start_ns INTEGER,
            source_end_ns INTEGER,
            rows_processed INTEGER NOT NULL,
            error_code TEXT
        ) STRICT;
        CREATE TABLE scan_execution_inputs (
            scan_run_id TEXT PRIMARY KEY,
            monotonic_started REAL NOT NULL,
            monotonic_finished REAL NOT NULL,
            logs_json TEXT,
            traces_json TEXT,
            context_events_json TEXT,
            canonical_activities_json TEXT
        ) STRICT;
        CREATE TABLE source_session_records (
            scan_run_id TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            service_name TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_timestamp TEXT NOT NULL,
            source_timestamp_ns TEXT,
            context_evidence_id TEXT
        ) STRICT;
        CREATE TABLE session_context_events (
            event_id TEXT PRIMARY KEY,
            producer TEXT NOT NULL,
            occurred_at TEXT NOT NULL
        ) STRICT;
        CREATE TABLE canonical_rejections (
            id TEXT PRIMARY KEY,
            producer TEXT NOT NULL,
            producer_surface TEXT NOT NULL,
            lifecycle_event TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            source_adapter TEXT NOT NULL
        ) STRICT;
        CREATE TABLE maintenance_observations (
            event_id TEXT NOT NULL,
            database_identity TEXT NOT NULL,
            check_type TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            result TEXT NOT NULL,
            backup_completed_at TEXT,
            database_bytes INTEGER NOT NULL,
            wal_bytes INTEGER,
            freelist_count INTEGER NOT NULL,
            page_count INTEGER NOT NULL,
            migration_state TEXT,
            runtime_host TEXT NOT NULL
        ) STRICT;
        CREATE TABLE native_pipeline_snapshots (
            event_id TEXT PRIMARY KEY,
            scan_run_id TEXT NOT NULL
        ) STRICT;
        """
    )


def _copy_native_pipeline_snapshots(
    source: sqlite3.Connection, target: sqlite3.Connection, request: LiveProofRequest
) -> None:
    """Retain native snapshot identity solely as scan lineage, never scalar authority."""
    if not _has_columns(source, "otlp_outbox", _NATIVE_PIPELINE_SNAPSHOT_COLUMNS):
        return
    rows = source.execute(
        """
        SELECT outbox.event_id, scans.id
        FROM otlp_outbox AS outbox
        JOIN scan_runs AS scans
          ON json_extract(outbox.payload_json, '$."entity.id"') = scans.id
        WHERE json_valid(outbox.payload_json)
          AND outbox.event_id IS NOT NULL
          AND json_extract(outbox.payload_json, '$."event.name"') = ?
          AND scans.completed_at > ? AND scans.completed_at <= ?
        ORDER BY scans.completed_at, scans.id, outbox.event_id
        """,
        (
            _NATIVE_PIPELINE_SNAPSHOT_EVENT,
            request.start.astimezone(UTC).isoformat(),
            request.end.astimezone(UTC).isoformat(),
        ),
    )
    target.executemany("INSERT INTO native_pipeline_snapshots VALUES (?, ?)", rows)


def _copy_execution_inputs(
    source: sqlite3.Connection, target: sqlite3.Connection, request: LiveProofRequest
) -> None:
    """Copy schema-19 scan acquisitions verbatim, including ordered duplicate histories."""
    if not _has_columns(source, "scan_execution_inputs", _EXECUTION_INPUT_COLUMNS):
        return
    rows = source.execute(
        """
        SELECT inputs.scan_run_id, inputs.monotonic_started, inputs.monotonic_finished,
               inputs.logs_json, inputs.traces_json, inputs.context_events_json,
               inputs.canonical_activities_json
        FROM scan_execution_inputs AS inputs
        JOIN scan_runs AS scans ON scans.id = inputs.scan_run_id
        WHERE scans.completed_at > ? AND scans.completed_at <= ?
        ORDER BY scans.completed_at, scans.id
        """,
        (request.start.astimezone(UTC).isoformat(), request.end.astimezone(UTC).isoformat()),
    )
    target.executemany("INSERT INTO scan_execution_inputs VALUES (?, ?, ?, ?, ?, ?, ?)", rows)


def _copy_scans(
    source: sqlite3.Connection, target: sqlite3.Connection, request: LiveProofRequest
) -> None:
    if not _has_columns(source, "scan_runs", _SCAN_COLUMNS):
        return
    rows = source.execute(
        """
        SELECT id, status, started_at, completed_at, source_start_ns, source_end_ns,
               rows_processed, error_code
        FROM scan_runs
        WHERE completed_at > ? AND completed_at <= ?
        ORDER BY completed_at, id
        """,
        (request.start.astimezone(UTC).isoformat(), request.end.astimezone(UTC).isoformat()),
    )
    target.executemany("INSERT INTO scan_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)


def _copy_source_sessions(
    source: sqlite3.Connection, target: sqlite3.Connection, request: LiveProofRequest
) -> None:
    if not _has_columns(source, "source_session_records", _SOURCE_SESSION_COLUMNS):
        return
    rows = source.execute(
        """
        SELECT records.scan_run_id, records.source_kind, records.service_name,
               records.source_id, records.source_timestamp, records.source_timestamp_ns,
               records.context_evidence_id
        FROM source_session_records AS records
        JOIN scan_runs AS scans ON scans.id = records.scan_run_id
        WHERE scans.completed_at > ? AND scans.completed_at <= ?
        ORDER BY records.scan_run_id, records.source_kind, records.service_name,
                 records.source_timestamp, records.source_id
        """,
        (request.start.astimezone(UTC).isoformat(), request.end.astimezone(UTC).isoformat()),
    )
    target.executemany("INSERT INTO source_session_records VALUES (?, ?, ?, ?, ?, ?, ?)", rows)


def _copy_context_events(
    source: sqlite3.Connection, target: sqlite3.Connection, request: LiveProofRequest
) -> None:
    if not _has_columns(source, "session_context_events", _CONTEXT_COLUMNS):
        return
    rows = source.execute(
        """
        SELECT DISTINCT events.event_id, events.producer, events.occurred_at
        FROM session_context_events AS events
        JOIN source_session_records AS records
          ON records.context_evidence_id = events.event_id
        JOIN scan_runs AS scans ON scans.id = records.scan_run_id
        WHERE scans.completed_at > ? AND scans.completed_at <= ?
        ORDER BY events.producer, events.occurred_at, events.event_id
        """,
        (request.start.astimezone(UTC).isoformat(), request.end.astimezone(UTC).isoformat()),
    )
    target.executemany("INSERT INTO session_context_events VALUES (?, ?, ?)", rows)


def _has_columns(connection: sqlite3.Connection, table: str, required: tuple[str, ...]) -> bool:
    try:
        columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
    except sqlite3.DatabaseError:
        return False
    return set(required) <= columns


def _copy_rejections(
    source: sqlite3.Connection, target: sqlite3.Connection, request: LiveProofRequest
) -> None:
    if not _has_columns(source, "canonical_rejections", _REJECTION_COLUMNS):
        return
    rows = source.execute(
        """
        SELECT id, producer, producer_surface, lifecycle_event, occurred_at, reason_code,
               source_adapter
        FROM canonical_rejections
        WHERE occurred_at > ? AND occurred_at <= ?
        ORDER BY occurred_at, id
        """,
        (request.start.astimezone(UTC).isoformat(), request.end.astimezone(UTC).isoformat()),
    )
    target.executemany("INSERT INTO canonical_rejections VALUES (?, ?, ?, ?, ?, ?, ?)", rows)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _capture_maintenance(target: sqlite3.Connection, source: sqlite3.Connection) -> None:
    database_path = str(source.execute("PRAGMA database_list").fetchone()[2])
    page_count = int(source.execute("PRAGMA page_count").fetchone()[0])
    page_size = int(source.execute("PRAGMA page_size").fetchone()[0])
    freelist_count = int(source.execute("PRAGMA freelist_count").fetchone()[0])
    quick_check = tuple(str(row[0]) for row in source.execute("PRAGMA quick_check"))
    database_identity = hashlib.sha256(database_path.encode()).hexdigest()
    event_id = hashlib.sha256(
        f"{database_identity}\x1f{_utc_now()}\x1fquick_check".encode()
    ).hexdigest()
    target.execute(
        "INSERT INTO maintenance_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            event_id,
            database_identity,
            "integrity",
            _utc_now(),
            "passed" if quick_check == ("ok",) else "failed",
            None,
            page_count * page_size,
            Path(f"{database_path}-wal").stat().st_size
            if Path(f"{database_path}-wal").exists()
            else 0,
            freelist_count,
            page_count,
            None,
            hashlib.sha256(socket.gethostname().encode()).hexdigest(),
        ),
    )


@dataclass(frozen=True, slots=True)
class DrainCapture:
    """Pre-call selection for one observed experiment drain, never a transport."""

    target: sqlite3.Connection
    drain_id: str
    started_at: datetime
    selected: tuple[tuple[object, ...], ...]

    def finish(self, rows: Sequence[Sequence[object]]) -> datetime:
        """Freeze committed successes without inventing a failed-attempt instant."""
        before = {row[0]: row for row in self.selected}
        completed_at = datetime.now(UTC)
        failure_timing_authoritative = True
        with self.target:
            for row in rows:
                prior = before[row[0]]
                delivered = row[2] == "delivered" and row[6] != prior[6]
                failed = row[3] == prior[3] + 1 if isinstance(prior[3], int) else False
                if delivered:
                    self.target.execute(
                        "INSERT INTO otlp_outbox_delivery_attempts VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            str(uuid.uuid4()),
                            row[0],
                            self.drain_id,
                            row[6],
                            "delivered",
                            None,
                        ),
                    )
                if failed:
                    failure_timing_authoritative = False
                self.target.execute(
                    "UPDATE otlp_outbox SET status=?, attempt_count=?, next_attempt_at=?, "
                    "delivered_at=? WHERE event_id=?",
                    (row[2], row[3], row[4], row[6], row[0]),
                )
            self.target.execute(
                "INSERT INTO otlp_outbox_drains VALUES (?, ?, ?, ?)",
                (
                    self.drain_id,
                    completed_at.isoformat(),
                    1,
                    int(failure_timing_authoritative),
                ),
            )
        for table in ("otlp_outbox", "otlp_outbox_delivery_attempts", "otlp_outbox_drains"):
            self.target.executescript(
                f"CREATE TRIGGER {table}_immutable_update BEFORE UPDATE ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'immutable drain observation'); END;"
                f"CREATE TRIGGER {table}_immutable_delete BEFORE DELETE ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'immutable drain observation'); END;"
            )
        return completed_at


def begin_drain_capture(
    target: sqlite3.Connection,
    rows: Sequence[Sequence[object]],
    events: Sequence[DerivedEvent],
) -> DrainCapture:
    """Persist actual fresh pre-state before the existing exact delivery path runs."""
    if not events or any(
        not event.event_name.startswith("dashboard_prototype.")
        or event.attributes.get("dashboard.event_kind") != "primitive"
        for event in events
    ):
        raise PipelineCaptureError("only experiment primitive delivery may be observed")
    ids = {event.event_id for event in events}
    if len(ids) != len(events) or {row[0] for row in rows} != ids:
        raise PipelineCaptureError("drain observation requires the complete unique population")
    if any(row[2] != "pending" or row[3] != 0 for row in rows):
        raise PipelineCaptureError("new drain observation cannot backfill previous attempts")
    target.executescript(
        """
        CREATE TABLE otlp_outbox (
            event_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, status TEXT NOT NULL,
            attempt_count INTEGER NOT NULL, next_attempt_at TEXT NOT NULL,
            created_at TEXT NOT NULL, delivered_at TEXT, destination TEXT NOT NULL,
            event_type TEXT NOT NULL
        ) STRICT;
        CREATE TABLE otlp_outbox_delivery_attempts (
            attempt_id TEXT PRIMARY KEY, event_id TEXT NOT NULL, drain_id TEXT NOT NULL,
            attempted_at TEXT NOT NULL, status TEXT NOT NULL, error_class TEXT
        ) STRICT;
        CREATE TABLE otlp_outbox_drains (
            drain_id TEXT PRIMARY KEY, completed_at TEXT NOT NULL, is_final INTEGER NOT NULL,
            failure_timing_authoritative INTEGER NOT NULL
        ) STRICT;
        """
    )
    with target:
        target.executemany(
            "INSERT INTO otlp_outbox VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ((*row, "signoz-otlp-http", json.loads(str(row[1]))["event.name"]) for row in rows),
        )
    return DrainCapture(target, str(uuid.uuid4()), datetime.now(UTC), tuple(map(tuple, rows)))

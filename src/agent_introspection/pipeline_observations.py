"""Immutable operational observations captured at the end of a scan."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from agent_introspection.database import DatabaseError, latest_backup_observation, quick_check
from agent_introspection.ledger_identity import database_identity
from agent_introspection.pipeline_events import parse_timestamp
from agent_introspection.source import CANONICAL_SERVICE_PRODUCERS
from agent_introspection.telemetry import OPERATIONAL_SCOPE, DerivedEvent, EventQueryClient

_SCHEMA = 2
_PIPELINE_PRODUCERS = frozenset({"omp", "codex-cli", "codex-app-server"})
SOURCE_LAG_COHORTS = (
    ("codex-app-server", "codex-app-server", "logs"),
    ("codex-app-server", "codex-app-server", "traces"),
    ("codex-app-server", "codex-app-server", "lifecycle"),
    ("codex-cli", "codex-cli", "logs"),
    ("codex-cli", "codex-cli", "traces"),
    # Notify session_context supplies a timestamp, not an inferred lifecycle interval.
    ("codex-cli", "codex-cli", "lifecycle"),
    ("omp", "omp", "logs"),
    ("omp", "omp", "traces"),
    ("omp", "omp", "lifecycle"),
)


def _datetime_ns(value: datetime) -> int:
    utc = value.astimezone(UTC)
    delta = utc - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


def _ns(value: int | str) -> str:
    """Keep nanoseconds exact across OTLP's Float64 attribute transport."""
    return str(int(value))


def _event(
    entity_id: str,
    version: int,
    name: str,
    timestamp_ns: int,
    attributes: dict[str, str | int | float | bool],
) -> DerivedEvent:
    return DerivedEvent(OPERATIONAL_SCOPE, entity_id, version, 1, name, attributes, timestamp_ns)


def capture_source_lag(
    *,
    connection: sqlite3.Connection,
    client: EventQueryClient,
    extraction_bound_ns: int,
) -> dict[tuple[str, str, str], int]:
    """Read source timestamps before the scan's completion boundary."""
    latest: dict[tuple[str, str, str], int] = {}
    services = tuple(
        service
        for service, producer_surface in CANONICAL_SERVICE_PRODUCERS.items()
        if producer_surface[0] in _PIPELINE_PRODUCERS
    )
    services_sql = ", ".join(f"'{service}'" for service in sorted(services))
    log_sql = f"""
        SELECT resource.`service.name`::String AS service_name,
               toUInt64(max(timestamp)) AS timestamp_ns
        FROM signoz_logs.distributed_logs_v2
        WHERE timestamp <= {{bound:UInt64}}
          AND resource.`service.name`::String IN ({services_sql})
        GROUP BY service_name
    """
    trace_sql = f"""
        SELECT serviceName AS service_name,
               toUInt64(toUnixTimestamp64Nano(max(timestamp))) AS timestamp_ns
        FROM signoz_traces.distributed_signoz_index_v3
        WHERE timestamp <= fromUnixTimestamp64Nano({{bound:Int64}})
          AND serviceName IN ({services_sql})
        GROUP BY service_name
    """
    for signal, sql in (("logs", log_sql), ("traces", trace_sql)):
        for row in client.query(sql, {"bound": extraction_bound_ns}):
            service = row.get("service_name")
            timestamp_ns = parse_timestamp(row.get("timestamp_ns"))
            producer_surface = CANONICAL_SERVICE_PRODUCERS.get(str(service))
            if producer_surface is None or timestamp_ns > extraction_bound_ns:
                raise ValueError("invalid authoritative source-lag aggregate")
            key = producer_surface[0], producer_surface[1], signal
            latest[key] = max(timestamp_ns, latest.get(key, timestamp_ns))
    bound_time = datetime.fromtimestamp(extraction_bound_ns // 1_000_000_000, tz=UTC).replace(
        microsecond=(extraction_bound_ns % 1_000_000_000) // 1_000
    )
    for producer, occurred_at in connection.execute(
        """SELECT producer, max(occurred_at)
        FROM session_context_events
        WHERE producer IN ('omp', 'codex-cli', 'codex-app-server')
          AND occurred_at <= ?
        GROUP BY producer""",
        (bound_time.isoformat(),),
    ):
        source_ns = _datetime_ns(datetime.fromisoformat(str(occurred_at)))
        if source_ns <= extraction_bound_ns:
            latest[(str(producer), str(producer), "lifecycle")] = source_ns
    return latest


def source_lag_events(
    latest: dict[tuple[str, str, str], int],
    *,
    scan_run_id: str,
    extraction_bound_ns: int,
    completed_at_ns: int,
) -> list[DerivedEvent]:
    """Bind successful source observations to the canonical scan completion."""
    events: list[DerivedEvent] = []
    for sequence, (producer, surface, signal) in enumerate(SOURCE_LAG_COHORTS, 1):
        source_ns = latest.get((producer, surface, signal))
        lag_ns = None if source_ns is None else extraction_bound_ns - source_ns
        attributes: dict[str, str | int | float | bool] = {
            "pipeline.payload_schema_version": _SCHEMA,
            "scan.run_id": scan_run_id,
            "scan.extraction_bound_ns": _ns(extraction_bound_ns),
            "scan.completed_at_ns": _ns(completed_at_ns),
            "source.producer": producer,
            "source.surface": surface,
            "source.signal": signal,
            "source.capability": "supported",
            "source.lag_state": (
                "absent" if lag_ns is None else ("clock_skew" if lag_ns < 0 else "available")
            ),
        }
        if source_ns is not None and lag_ns is not None:
            attributes["source.latest_timestamp_ns"] = _ns(source_ns)
            attributes["source.lag_ns"] = _ns(lag_ns)
        events.append(
            _event(
                f"{scan_run_id}:lag:{sequence}",
                1,
                "introspection.pipeline.source_lag",
                completed_at_ns,
                attributes,
            )
        )
    return events


def _ledger_measurements(
    connection: sqlite3.Connection, database_path: Path
) -> dict[str, str | int | float | bool]:
    attributes: dict[str, str | int | float | bool] = {}
    for key, sql in (
        ("database.page_count", "PRAGMA page_count"),
        ("database.freelist_count", "PRAGMA freelist_count"),
        ("database.migration_version", "SELECT COALESCE(MAX(version), 0) FROM migrations"),
    ):
        try:
            attributes[key] = int(connection.execute(sql).fetchone()[0])
        except sqlite3.DatabaseError as exc:
            attributes[f"{key}.error_class"] = type(exc).__name__
    for key, path in (
        ("database.bytes", database_path),
        ("database.wal_bytes", Path(f"{database_path}-wal")),
    ):
        try:
            attributes[key] = path.stat().st_size
        except FileNotFoundError:
            attributes[f"{key}.error_class"] = "FileNotFoundError"
        except OSError as exc:
            attributes[f"{key}.error_class"] = type(exc).__name__
    return attributes


def capture_maintenance(
    connection: sqlite3.Connection,
    *,
    scan_run_id: str,
    database_path: Path,
    runtime_identity: str,
) -> DerivedEvent:
    """Capture the actual check outcome without preventing terminal scan evidence."""
    database_path = database_path.expanduser().resolve()
    identity = database_identity(connection, database_path=database_path)
    check_error: str | None
    try:
        quick_check(connection)
    except (DatabaseError, sqlite3.DatabaseError) as exc:
        check_result = "failed"
        check_error = type(exc).__name__
    else:
        check_result = "ok"
        check_error = None
    check_completed_at_ns = _datetime_ns(datetime.now(UTC))
    attributes: dict[str, str | int | float | bool] = {
        "pipeline.payload_schema_version": _SCHEMA,
        "database.identity": identity,
        "database.check_type": "quick_check",
        "database.check_result": check_result,
        "database.check_completed_at_ns": _ns(check_completed_at_ns),
        **_ledger_measurements(connection, database_path),
        "scan.run_id": scan_run_id,
        "scan.runtime_identity": runtime_identity,
        "backup.state": "unknown",
    }
    try:
        backup = latest_backup_observation(connection, database_path)
    except (DatabaseError, sqlite3.DatabaseError, ValueError) as exc:
        attributes["backup.state"] = "unavailable"
        attributes["backup.error_class"] = type(exc).__name__
    else:
        if backup is not None:
            attributes.update(backup)
    if check_error is not None:
        attributes["database.check_error_class"] = check_error
    attributes["database.capture_state"] = (
        "partial"
        if check_error is not None or any(key.endswith(".error_class") for key in attributes)
        else "complete"
    )
    return _event(
        f"{scan_run_id}:maintenance",
        1,
        "introspection.pipeline.ledger_maintenance",
        _datetime_ns(datetime.now(UTC)),
        attributes,
    )


def capture_lifecycle_authority(
    connection: sqlite3.Connection, *, observed_at_ns: int, scan_run_id: str, runtime_identity: str
) -> list[DerivedEvent]:
    """Build interval versions; persist their FK ledger after event enqueue."""
    events: list[DerivedEvent] = []
    intervals = connection.execute(
        """SELECT event_id, producer, session_id, started_at, ended_at, project_id, project_name
        FROM session_context_intervals WHERE producer IN ('omp', 'codex-app-server')
        ORDER BY producer, session_id, event_id"""
    ).fetchall()
    for (
        opening_id,
        producer,
        session_id,
        started_at,
        ended_at,
        project_id,
        project_name,
    ) in intervals:
        fingerprint = hashlib.sha256(
            f"{producer}\x1f{session_id}\x1f{opening_id}\x1f{started_at}\x1f"
            f"{ended_at or ''}\x1f{project_id}\x1f{project_name}".encode()
        ).hexdigest()
        prior = connection.execute(
            "SELECT version, fingerprint FROM pipeline_lifecycle_interval_versions "
            "WHERE opening_event_id = ?",
            (opening_id,),
        ).fetchone()
        if prior is not None and str(prior[1]) == fingerprint:
            continue
        version = 1 if prior is None else int(prior[0]) + 1
        start_ns = _datetime_ns(datetime.fromisoformat(str(started_at)))
        end_ns = None if ended_at is None else _datetime_ns(datetime.fromisoformat(str(ended_at)))
        events.append(
            _event(
                f"lifecycle:{producer}:{session_id}:{opening_id}",
                version,
                "introspection.session_context.interval.recorded",
                start_ns,
                {
                    "interval.payload_schema_version": _SCHEMA,
                    "producer": str(producer),
                    "producer.surface": str(producer),
                    "session.id": str(session_id),
                    "interval.opening_event_id": str(opening_id),
                    "interval.fingerprint": fingerprint,
                    "interval.started_at": str(started_at),
                    "interval.start_ns": _ns(start_ns),
                    "interval.state": "closed" if end_ns is not None else "open",
                    "interval.version": _ns(version),
                    "agent.project.id": str(project_id),
                    "agent.project.name": str(project_name),
                    **(
                        {"interval.end_ns": _ns(end_ns), "interval.ended_at": str(ended_at)}
                        if end_ns is not None
                        else {}
                    ),
                },
            )
        )
    for (
        original_id,
        replacement_id,
        producer,
        session_id,
        original_kind,
        replacement_kind,
        replacement_producer,
        replacement_session,
    ) in connection.execute(
        """SELECT s.original_event_id, s.replacement_event_id,
            original.producer, original.session_id, original.event_type, replacement.event_type,
            replacement.producer, replacement.session_id
        FROM session_context_event_supersessions s
        JOIN session_context_events original ON original.event_id = s.original_event_id
        JOIN session_context_events replacement ON replacement.event_id = s.replacement_event_id
        WHERE original.producer IN ('omp', 'codex-app-server')
        ORDER BY s.original_event_id"""
    ):
        if (producer, session_id) != (replacement_producer, replacement_session):
            raise ValueError("lifecycle supersession changes native identity")
        if connection.execute(
            "SELECT 1 FROM pipeline_lifecycle_supersessions WHERE original_event_id = ?",
            (original_id,),
        ).fetchone():
            continue
        events.append(
            _event(
                f"lifecycle-supersession:{original_id}",
                1,
                "introspection.session_context.superseded",
                observed_at_ns,
                {
                    "interval.payload_schema_version": _SCHEMA,
                    "source.entity_id": str(original_id),
                    "replacement.event_id": str(replacement_id),
                    "supersession.version": _ns(1),
                    "producer": str(producer),
                    "producer.surface": str(producer),
                    "session.id": str(session_id),
                    "source.lifecycle_event": str(original_kind),
                    "replacement.lifecycle_event": str(replacement_kind),
                },
            )
        )
    events.append(
        _lifecycle_population(
            connection,
            events,
            scan_run_id=scan_run_id,
            runtime_identity=runtime_identity,
            observed_at_ns=observed_at_ns,
        )
    )
    return events


def _lifecycle_population(
    connection: sqlite3.Connection,
    events: list[DerivedEvent],
    *,
    scan_run_id: str,
    runtime_identity: str,
    observed_at_ns: int,
) -> DerivedEvent:
    intervals = dict(
        connection.execute(
            "SELECT opening_event_id, event_id FROM pipeline_lifecycle_interval_versions"
        )
    )
    supersessions = dict(
        connection.execute(
            "SELECT original_event_id, event_id FROM pipeline_lifecycle_supersessions"
        )
    )
    for event in events:
        if event.event_name == "introspection.session_context.interval.recorded":
            intervals[event.attributes["interval.opening_event_id"]] = event.event_id
        elif event.event_name == "introspection.session_context.superseded":
            supersessions[event.attributes["source.entity_id"]] = event.event_id
    return _event(
        f"{scan_run_id}:lifecycle-population",
        1,
        "introspection.session_context.population",
        observed_at_ns,
        {
            "interval.payload_schema_version": _SCHEMA,
            "scan.run_id": scan_run_id,
            "scan.runtime_identity": runtime_identity,
            "lifecycle.observed_at_ns": _ns(observed_at_ns),
            "lifecycle.interval_count": len(intervals),
            "lifecycle.supersession_count": len(supersessions),
            "lifecycle.interval_event_ids": json.dumps(
                sorted(intervals.values()), separators=(",", ":")
            ),
            "lifecycle.supersession_event_ids": json.dumps(
                sorted(supersessions.values()), separators=(",", ":")
            ),
        },
    )


def record_observation_events(
    connection: sqlite3.Connection, events: Iterable[DerivedEvent], *, observed_at_ns: int
) -> None:
    """Record local ownership only after referenced immutable events exist."""
    for event in events:
        if event.event_name == "introspection.pipeline.integrity_audit":
            connection.execute(
                """INSERT INTO pipeline_integrity_audits
                (scan_run_id, event_id, observed_at_ns, physical_count,
                 incident_count, incident_event_ids_json) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    event.attributes["scan.run_id"],
                    event.event_id,
                    str(event.timestamp_ns),
                    event.attributes["audit.physical_count"],
                    event.attributes["audit.incident_count"],
                    event.attributes["audit.incident_event_ids"],
                ),
            )
        elif event.event_name == "introspection.session_context.interval.recorded":
            opening_id = event.attributes["interval.opening_event_id"]
            connection.execute(
                """INSERT INTO pipeline_lifecycle_interval_versions
                (opening_event_id, version, fingerprint, event_id) VALUES (?, ?, ?, ?)
                ON CONFLICT(opening_event_id) DO UPDATE SET version=excluded.version,
                fingerprint=excluded.fingerprint, event_id=excluded.event_id""",
                (
                    opening_id,
                    int(str(event.attributes["interval.version"])),
                    event.attributes["interval.fingerprint"],
                    event.event_id,
                ),
            )
        elif event.event_name == "introspection.session_context.superseded":
            connection.execute(
                """INSERT INTO pipeline_lifecycle_supersessions
                (original_event_id, replacement_event_id, event_id) VALUES (?, ?, ?)
                ON CONFLICT(original_event_id) DO NOTHING""",
                (
                    event.attributes["source.entity_id"],
                    event.attributes["replacement.event_id"],
                    event.event_id,
                ),
            )

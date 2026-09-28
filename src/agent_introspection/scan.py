"""Transactional extraction, detection, trend evaluation, and derived events."""

from __future__ import annotations

import hashlib
import json
import signal
import sqlite3
import time
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from agent_introspection import scheduler
from agent_introspection.attribution import (
    canonical_activity_event_attributes,
    reconcile_activity,
    resolve_attribution,
    resolve_metric_attribution,
)
from agent_introspection.capabilities import (
    CapabilityError,
    discover_source_schema,
    enforce_approved_schema,
    verify_network_perimeter,
)
from agent_introspection.config import AppConfig
from agent_introspection.database import (
    CanonicalActivity,
    CanonicalAttribution,
    CanonicalSourceMembership,
    persist_canonical_activity,
    quick_check,
)
from agent_introspection.detectors import DetectorEngine, DetectorEvent, Observation
from agent_introspection.identities import ProjectIdentity, canonical_task
from agent_introspection.normalization import NormalizationError, normalize_tool_operation
from agent_introspection.outcomes import derive_outcome
from agent_introspection.pipeline_delivery import (
    FinalDrainResult,
    capture_final_drain,
    enqueue_final_drain_projection,
)
from agent_introspection.pipeline_integrity import (
    INTEGRITY_INCIDENT_EVENT,
    capture_remote_integrity,
)
from agent_introspection.pipeline_observations import (
    capture_lifecycle_authority,
    capture_maintenance,
    capture_source_lag,
    record_observation_events,
    source_lag_events,
)
from agent_introspection.pipeline_runtime import implementation_fingerprint
from agent_introspection.session_context import drain_inbox, inbox_path
from agent_introspection.source import (
    CANONICAL_SERVICE_PRODUCERS,
    ClickHouseClient,
    HydrationRequest,
    HydrationRow,
    LogRow,
    SourceSessionRow,
    TraceRow,
)
from agent_introspection.telemetry import (
    OPERATIONAL_SCOPE,
    CanonicalActivityVersionEvent,
    DerivedEvent,
    enqueue_canonical_activity_version,
    enqueue_events,
)
from agent_introspection.trends import (
    TrendEvaluation,
    recompute_canonical_findings,
)


class ScanError(RuntimeError):
    """A scan cannot safely commit its extraction window."""


def _datetime_ns(value: datetime) -> int:
    utc = value.astimezone(UTC)
    delta = utc - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


class ScanDeadlineError(ScanError):
    """A scan exceeded its bounded execution window."""


_SCAN_TIMEOUT_SECONDS = 900.0
_ACTIVITY_FORWARD_WINDOW_SECONDS = 900


def _arm_scan_deadline() -> tuple[Any, tuple[float, float]]:
    """Arm the process-wide deadline that bounds all scan work."""
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    if previous_timer != (0.0, 0.0):
        raise ScanError("scan deadline timer is already active")
    previous_handler = signal.getsignal(signal.SIGALRM)

    def expire(_signum: int, _frame: object) -> None:
        raise ScanDeadlineError(f"scan exceeded {_SCAN_TIMEOUT_SECONDS:.0f} second deadline")

    signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, _SCAN_TIMEOUT_SECONDS)
    return previous_handler, previous_timer


def _disarm_scan_deadline(state: tuple[Any, tuple[float, float]]) -> None:
    """Restore the process signal state after a terminal scan outcome."""
    previous_handler, previous_timer = state
    signal.setitimer(signal.ITIMER_REAL, 0)
    signal.signal(signal.SIGALRM, previous_handler)
    if previous_timer != (0.0, 0.0):
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)


@dataclass(frozen=True, slots=True)
class TrendEventRecord:
    evaluation: TrendEvaluation
    promoted: bool
    entity_version: int
    category: str
    project_id: str | None
    detector_id: str


@dataclass(slots=True)
class PipelineStream:
    """Safe terminal state for one bounded source query."""

    query_status: str = "unknown"
    data_state: str = "unknown"
    latest_timestamp_ns: int | None = None


@dataclass(frozen=True, slots=True)
class _PipelineSnapshotRequest:
    scan_run_id: str
    end_ns: int
    terminal_status: str
    error_class: str | None
    logs: PipelineStream
    traces: PipelineStream
    hydration: PipelineStream
    finished_ns: int
    duration_ms: float
    rows_processed: int | None
    logs_count: int | None
    traces_count: int | None
    context_events_count: int | None
    canonical_activities_count: int | None
    pending_after_drain: int | None
    failed_during_drain: int | None
    runtime_identity: str
    schedule_interval_seconds: int
    schedule_timezone: str


def _snapshot_attributes(
    request: _PipelineSnapshotRequest,
    *,
    freshness: str,
    logs_lag: tuple[str, int | None],
    traces_lag: tuple[str, int | None],
) -> dict[str, str | int | float | bool]:
    logs_lag_state, logs_lag_ms = logs_lag
    traces_lag_state, traces_lag_ms = traces_lag
    attributes: dict[str, str | int | float | bool] = {
        "pipeline.payload_schema_version": 2,
        "pipeline.state": _pipeline_state(
            terminal_status=request.terminal_status,
            freshness=freshness,
            logs=request.logs,
            traces=request.traces,
            hydration=request.hydration,
        ),
        "scan.terminal_status": request.terminal_status,
        "scan.completed_at_ns": str(request.finished_ns),
        "scan.extraction_bound_ns": str(request.end_ns),
        "scan.runtime_identity": request.runtime_identity,
        "scan.schedule_interval_seconds": request.schedule_interval_seconds,
        "scan.schedule_timezone": request.schedule_timezone,
        "pipeline.freshness": freshness,
        "logs.query_status": request.logs.query_status,
        "logs.data_state": request.logs.data_state,
        "traces.query_status": request.traces.query_status,
        "traces.data_state": request.traces.data_state,
        "hydration.query_status": request.hydration.query_status,
        "hydration.data_state": request.hydration.data_state,
        "logs.lag_state": logs_lag_state,
        "traces.lag_state": traces_lag_state,
        "scan.duration_ms": request.duration_ms,
        "rows.data_state": ("records" if request.rows_processed else "no_data")
        if request.rows_processed is not None
        else "unknown",
        "context.data_state": ("records" if request.context_events_count else "no_data")
        if request.context_events_count is not None
        else "unknown",
        "canonical.activities_data_state": (
            "records" if request.canonical_activities_count else "no_data"
        )
        if request.canonical_activities_count is not None
        else "unknown",
        "outbox.drain_state": (
            "completed" if request.pending_after_drain is not None else "unavailable"
        ),
    }
    measured_counts = (
        ("rows.processed", request.rows_processed),
        ("logs.count", request.logs_count),
        ("traces.count", request.traces_count),
        ("context.events_count", request.context_events_count),
        ("canonical.activities_count", request.canonical_activities_count),
    )
    attributes.update({key: value for key, value in measured_counts if value is not None})
    optional = (
        ("outbox.pending_after_drain", request.pending_after_drain),
        ("outbox.failed_during_drain", request.failed_during_drain),
        ("pipeline.error_class", request.error_class),
        (
            "logs.latest_timestamp_ns",
            None
            if request.logs.latest_timestamp_ns is None
            else str(request.logs.latest_timestamp_ns),
        ),
        (
            "traces.latest_timestamp_ns",
            None
            if request.traces.latest_timestamp_ns is None
            else str(request.traces.latest_timestamp_ns),
        ),
        ("logs.lag_ms", logs_lag_ms),
        ("traces.lag_ms", traces_lag_ms),
    )
    attributes.update({key: value for key, value in optional if value is not None})
    return attributes


def _pipeline_runtime_identity(config: AppConfig) -> str:
    """Return a stable redacted scanner identity, never a filesystem path."""
    return hashlib.sha256(
        f"{config.database.path.resolve()}|{config.signoz.clickhouse_container}".encode()
    ).hexdigest()


def _stream_lag(stream: PipelineStream, *, finished_ns: int) -> tuple[str, int | None]:
    if stream.latest_timestamp_ns is None:
        return "not_applicable", None
    lag_ms = (finished_ns - stream.latest_timestamp_ns) // 1_000_000
    if lag_ms < 0:
        return "clock_skew", None
    return "available", int(lag_ms)


def _freshness(
    *,
    terminal_status: str,
    logs: PipelineStream,
    traces: PipelineStream,
    finished_ns: int,
) -> str:
    if terminal_status == "failed":
        return "missing"
    timestamps = [
        timestamp
        for timestamp in (logs.latest_timestamp_ns, traces.latest_timestamp_ns)
        if timestamp is not None
    ]
    if not timestamps:
        return "fresh"
    lag_ms = (finished_ns - max(timestamps)) // 1_000_000
    if lag_ms < 0:
        return "clock_skew"
    if lag_ms <= 3_900_000:
        return "fresh"
    if lag_ms <= 7_200_000:
        return "late"
    return "stale"


def _pipeline_state(
    *,
    terminal_status: str,
    freshness: str,
    logs: PipelineStream,
    traces: PipelineStream,
    hydration: PipelineStream,
) -> str:
    if terminal_status == "failed":
        return "unhealthy"
    if any(stream.query_status != "available" for stream in (logs, traces, hydration)):
        return "unhealthy"
    if freshness == "fresh":
        return "healthy"
    if freshness == "late":
        return "degraded"
    return "unhealthy"


def _pipeline_snapshot_event(request: _PipelineSnapshotRequest) -> DerivedEvent:
    logs_lag = _stream_lag(request.logs, finished_ns=request.finished_ns)
    traces_lag = _stream_lag(request.traces, finished_ns=request.finished_ns)
    freshness = _freshness(
        terminal_status=request.terminal_status,
        logs=request.logs,
        traces=request.traces,
        finished_ns=request.finished_ns,
    )
    return DerivedEvent(
        scope=OPERATIONAL_SCOPE,
        entity_id=request.scan_run_id,
        entity_version=1,
        event_sequence=1,
        event_name="introspection.pipeline.snapshot",
        attributes=_snapshot_attributes(
            request, freshness=freshness, logs_lag=logs_lag, traces_lag=traces_lag
        ),
        timestamp_ns=request.finished_ns,
    )


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _bounds(
    connection: sqlite3.Connection,
    snapshot_end_ns: int,
    *,
    initial_start_ns: int,
    replay_overlap_seconds: int,
) -> tuple[int, int, int, int]:
    """Bound detector queries to a jointly committed cursor and forward window."""
    rows = connection.execute(
        """
        SELECT timestamp_ns FROM source_watermarks
        WHERE source IN ('signoz_logs', 'signoz_raw_source_sessions')
        """
    ).fetchall()
    cursor_ns = max((int(row[0]) for row in rows), default=initial_start_ns)
    end_ns = (
        min(snapshot_end_ns, cursor_ns + _ACTIVITY_FORWARD_WINDOW_SECONDS * 1_000_000_000)
        if cursor_ns > 0
        else snapshot_end_ns
    )
    start_ns = max(0, cursor_ns - replay_overlap_seconds * 1_000_000_000)
    if start_ns >= end_ns:
        start_ns = max(0, end_ns - 1)
    start_bucket = max(0, start_ns // 1_000_000_000 - 1800)
    end_bucket = end_ns // 1_000_000_000
    return start_ns, end_ns, start_bucket, end_bucket


def _claim_raw_source_window(
    connection: sqlite3.Connection, *, end_ns: int
) -> tuple[int, int] | None:
    """Durably claim one exact raw-source window before querying ClickHouse."""
    pending = connection.execute(
        """
        SELECT claims.start_ns, claims.end_ns
        FROM raw_source_window_claims AS claims
        LEFT JOIN raw_source_window_completions AS completions
          ON completions.source = claims.source
         AND completions.start_ns = claims.start_ns
         AND completions.end_ns = claims.end_ns
        WHERE claims.source = 'signoz_raw_source_sessions'
          AND completions.source IS NULL
        ORDER BY claims.claimed_at, claims.start_ns, claims.end_ns
        LIMIT 1
        """
    ).fetchone()
    if pending is not None:
        return int(pending[0]), int(pending[1])
    row = connection.execute(
        "SELECT timestamp_ns FROM source_watermarks WHERE source = 'signoz_raw_source_sessions'"
    ).fetchone()
    start_ns = int(row[0]) if row is not None else _raw_source_anchor(connection)
    if start_ns >= end_ns:
        return None
    with connection:
        connection.execute(
            """
            INSERT INTO raw_source_window_claims (source, start_ns, end_ns, claimed_at)
            VALUES ('signoz_raw_source_sessions', ?, ?, ?)
            """,
            (start_ns, end_ns, _iso_now()),
        )
    return start_ns, end_ns


def _raw_source_anchor(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT start_ns FROM raw_source_window_anchors WHERE source = 'signoz_raw_source_sessions'"
    ).fetchone()
    if row is None:
        raise ScanError("raw source window requires an approved dual-stream anchor")
    return int(row[0])


def _approve_raw_source_anchor(
    connection: sqlite3.Connection, *, logs_earliest_ns: int, traces_earliest_ns: int
) -> None:
    start_ns = max(logs_earliest_ns, traces_earliest_ns)
    with connection:
        connection.execute(
            """
            INSERT INTO raw_source_window_anchors (
                source, start_ns, logs_earliest_ns, traces_earliest_ns, approved_at
            ) VALUES ('signoz_raw_source_sessions', ?, ?, ?, ?)
            ON CONFLICT(source) DO NOTHING
            """,
            (start_ns, logs_earliest_ns, traces_earliest_ns, _iso_now()),
        )


def _trace_indexes(logs: list[LogRow], traces: list[TraceRow]) -> dict[str, TraceRow]:
    del logs
    return {trace.trace_id: trace for trace in traces}


def _shortlisted_log_ids(logs: list[LogRow], by_trace: dict[str, TraceRow]) -> list[str]:
    explicit: set[str] = set()
    tool_groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for log in logs:
        if (
            (log.event_name == "codex.tool_result" and log.success_string == "false")
            or (
                log.event_name in {"codex.api_request", "codex.websocket_request"}
                and (
                    log.success_bool is False
                    or (log.status_code is not None and log.status_code >= 400)
                )
            )
            or log.event_name in {"codex.sandbox_outcome", "codex.tool_decision"}
        ):
            explicit.add(log.log_id)
        if log.tool_name:
            trace = by_trace.get(log.trace_id or "")
            task_hint = (
                trace.thread_id
                if trace is not None and trace.thread_id is not None
                else (
                    trace.correlation.correlation_id
                    if trace is not None and trace.correlation is not None
                    else log.trace_id or log.log_id
                )
            )
            tool_groups[(task_hint, log.tool_name)].append(log.log_id)
    for identifiers in tool_groups.values():
        if len(identifiers) >= 2:
            explicit.update(identifiers)
    return sorted(explicit)


def _hydrated_operations(rows: list[HydrationRow]) -> dict[str, Any]:
    operations: dict[str, Any] = {}
    for row in rows:
        arguments = row.arguments or row.args or row.argv
        if row.tool_name is None or arguments is None:
            continue
        try:
            operations[row.log_id] = normalize_tool_operation(
                row.tool_name,
                arguments,
                exit_code=row.exit_code,
                diagnostic_code=row.diagnostic_code,
            )
        except NormalizationError:
            continue
    return operations


def _detector_events(
    logs: list[LogRow],
    traces: list[TraceRow],
    hydration: list[HydrationRow],
) -> list[DetectorEvent]:
    by_trace = _trace_indexes(logs, traces)
    hydration_by_id = {row.log_id: row for row in hydration}
    operations = _hydrated_operations(hydration)
    events: list[DetectorEvent] = []
    mutation_tools = {"apply_patch", "write_file", "edit_file", "create_file"}
    for log in logs:
        trace = by_trace.get(log.trace_id or "")
        if trace is None or trace.correlation is None:
            continue
        task = canonical_task(
            trace_id=log.trace_id or log.log_id,
            thread_id=trace.thread_id if trace else None,
            conversation_id=None,
            conversation_to_thread={},
        )
        hydrated = hydration_by_id.get(log.log_id)
        event_name, outcome = derive_outcome(
            event_name=log.event_name,
            decision_source=log.decision_source,
            decision=log.decision,
            hydrated_outcome=hydrated.outcome if hydrated else None,
        )
        events.append(
            DetectorEvent(
                event_id=log.log_id,
                timestamp=datetime.fromtimestamp(log.timestamp_ns / 1_000_000_000, tz=UTC),
                project_id="canonical",
                task_id=task.canonical,
                event_name=event_name,
                operation=operations.get(log.log_id),
                success_string=log.success_string,
                success_bool=log.success_bool,
                status_code=log.status_code,
                outcome=outcome,
                is_mutation=bool(log.tool_name in mutation_tools),
                counts_as_distinct_task=task.counts_as_distinct_task,
                attribution_method="session_context",
            )
        )
    for trace in traces:
        if trace.correlation is None or trace.total_tokens <= 0:
            continue
        task = canonical_task(
            trace_id=trace.trace_id,
            thread_id=trace.thread_id,
            conversation_id=None,
            conversation_to_thread={},
        )
        events.append(
            DetectorEvent(
                event_id=f"trace:{trace.trace_id}",
                timestamp=trace.ended_at,
                project_id="canonical",
                task_id=task.canonical,
                event_name="trace.episode",
                token_count=trace.total_tokens,
                counts_as_distinct_task=task.counts_as_distinct_task,
                attribution_method="session_context",
            )
        )
    return events


def _partition_observations_by_context(
    connection: sqlite3.Connection,
    observations: tuple[Observation, ...],
    event_index: dict[str, DetectorEvent],
    traces_by_id: dict[str, TraceRow],
    logs_by_id: dict[str, LogRow],
) -> tuple[Observation, ...]:
    partitioned: list[Observation] = []
    for observation in observations:
        groups: dict[tuple[str, str], list[str]] = defaultdict(list)
        for event_id in observation.event_ids:
            trace_id = (
                event_id.removeprefix("trace:")
                if event_id.startswith("trace:")
                else (logs_by_id[event_id].trace_id or "")
            )
            trace = traces_by_id.get(trace_id)
            if trace is None or trace.correlation is None:
                groups[(f"unresolved:{event_id}", "unresolved")].append(event_id)
                continue
            attribution = resolve_attribution(
                connection,
                producer=trace.correlation.producer,
                correlation_id=trace.correlation.correlation_id,
                source_at=event_index[event_id].timestamp,
            )
            partition_key = attribution.evidence_id or f"unresolved:{event_id}"
            project_id = attribution.project_id or "unresolved"
            groups[(partition_key, project_id)].append(event_id)
        for (_, project_id), event_ids in groups.items():
            components = replace(observation.fingerprint_components, project_identity=project_id)
            partitioned.append(
                replace(
                    observation,
                    project_id=project_id,
                    task_ids=tuple(
                        sorted({event_index[event_id].task_id for event_id in event_ids})
                    ),
                    event_ids=tuple(event_ids),
                    fingerprint=components.digest(),
                    fingerprint_components=components,
                )
            )
    return tuple(partitioned)


def _persist_source_rejections(connection: sqlite3.Connection, traces: list[TraceRow]) -> None:
    for trace in traces:
        status = trace.correlation_status
        if status is None:
            continue
        reason_code = (
            "missing_correlation_id" if status.state == "missing" else "conflicting_correlation_id"
        )
        provenance = json.dumps(
            {
                "source_event_ids": [],
                "source_log_ids": [],
                "source_span_ids": list(status.source_span_ids),
            },
            separators=(",", ":"),
        )
        occurred_at = status.source_event_timestamp.isoformat()
        identity = (
            status.producer,
            status.producer_surface,
            None,
            "source_activity",
            occurred_at,
            reason_code,
            "signoz",
            provenance,
        )
        rejection_id = hashlib.sha256(
            json.dumps(identity, separators=(",", ":")).encode()
        ).hexdigest()
        connection.execute(
            """
            INSERT INTO canonical_rejections (
                id, producer, producer_surface, correlation_id, lifecycle_event, occurred_at,
                reason_code, source_adapter, source_provenance, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            (rejection_id, *identity, _iso_now()),
        )


def _source_session_identity(row: SourceSessionRow) -> str:
    return hashlib.sha256(
        json.dumps(
            (row.source_kind, row.service_name, row.source_id),
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _source_session_group_id(row: SourceSessionRow) -> str:
    """Return the deterministic identifier of this record's native-key candidate set."""
    return hashlib.sha256(
        json.dumps(row.native_session_ids, separators=(",", ":")).encode()
    ).hexdigest()


def _source_native_key(row: SourceSessionRow) -> tuple[str, str] | None:
    """Return the one canonical producer and native session for an exact raw record."""
    producer = CANONICAL_SERVICE_PRODUCERS.get(row.service_name)
    if producer is None or row.session_status != "exact":
        return None
    return producer[0], row.native_session_ids[0]


_SourceSessionTerminal = tuple[str, str, str | None, tuple[str, str, str, str] | None]
_ResolvedSourceSessionInterval = tuple[datetime, datetime | None, _SourceSessionTerminal]


@dataclass(frozen=True, slots=True)
class _SourceSessionResolutionRequest:
    connection: sqlite3.Connection
    row: SourceSessionRow
    clock_skew_seconds: int
    resolved_intervals: dict[tuple[str, str], list[_ResolvedSourceSessionInterval]] | None


def _source_session_input_terminal(row: SourceSessionRow) -> _SourceSessionTerminal | None:
    reasons = {
        "missing": "missing_native_session_id",
        "wrong_field": "wrong_native_session_field",
        "conflicting": "conflicting_native_session_id",
    }
    reason = reasons.get(row.session_status)
    return ("failed", reason, None, None) if reason is not None else None


def _cached_source_session_terminal(
    request: _SourceSessionResolutionRequest, cache_key: tuple[str, str]
) -> _SourceSessionTerminal | None:
    if request.resolved_intervals is None:
        return None
    source_at = request.row.source_timestamp.astimezone(UTC)
    for started_at, ended_at, terminal in request.resolved_intervals.get(cache_key, []):
        if started_at <= source_at and (ended_at is None or source_at < ended_at):
            return terminal
    return None


def _resolved_source_session_terminal(
    request: _SourceSessionResolutionRequest,
    producer: tuple[str, str],
    cache_key: tuple[str, str],
    evidence_id: str,
) -> _SourceSessionTerminal:
    project = request.connection.execute(
        """
        SELECT project_id, project_name, project_root, project_kind
        FROM session_context_intervals WHERE event_id = ?
        UNION
        SELECT project_id, project_name, project_root, project_kind
        FROM session_context_events WHERE event_id = ?
        """,
        (evidence_id, evidence_id),
    ).fetchone()
    if project is None:
        raise ScanError("resolved source session attribution lacks context evidence")
    terminal = ("attributed", "accepted_git_context", evidence_id, tuple(project))
    _cache_resolved_source_session(request, producer, cache_key, evidence_id, terminal)
    return terminal


def _cache_resolved_source_session(
    request: _SourceSessionResolutionRequest,
    producer: tuple[str, str],
    cache_key: tuple[str, str],
    evidence_id: str,
    terminal: _SourceSessionTerminal,
) -> None:
    if request.resolved_intervals is None:
        return
    interval = request.connection.execute(
        """
        SELECT started_at, ended_at
        FROM session_context_intervals
        WHERE event_id = ? AND producer = ? AND session_id = ?
          AND NOT EXISTS (
              SELECT 1 FROM session_context_event_supersessions AS supersession
              WHERE supersession.original_event_id = session_context_intervals.event_id
                 OR supersession.original_event_id = session_context_intervals.end_event_id
          )
        """,
        (evidence_id, producer[0], request.row.native_session_ids[0]),
    ).fetchone()
    if interval is not None:
        started_at = datetime.fromisoformat(str(interval[0])).astimezone(UTC)
        ended_at = datetime.fromisoformat(str(interval[1])).astimezone(UTC) if interval[1] else None
        request.resolved_intervals.setdefault(cache_key, []).append(
            (started_at, ended_at, terminal)
        )


def _source_session_failure_terminal(
    request: _SourceSessionResolutionRequest, producer: tuple[str, str], reason_code: str | None
) -> _SourceSessionTerminal:
    rejection = request.connection.execute(
        """
        SELECT id FROM canonical_rejections
        WHERE producer = ? AND producer_surface = ? AND correlation_id = ?
          AND reason_code = 'non_git_workspace'
        ORDER BY occurred_at, id LIMIT 1
        """,
        (producer[0], producer[1], request.row.native_session_ids[0]),
    ).fetchone()
    if rejection is not None:
        return "expected_rejection", "approved_non_git_workspace", str(rejection[0]), None
    reason = (
        "conflicting_correlation_id"
        if reason_code == "conflicting_correlation_id"
        else "no_authoritative_context"
    )
    return "failed", reason, None, None


def _source_session_terminal(request: _SourceSessionResolutionRequest) -> _SourceSessionTerminal:
    """Resolve a raw record through its canonical producer and one native session."""
    producer = CANONICAL_SERVICE_PRODUCERS.get(request.row.service_name)
    if producer is None:
        return "failed", "unmapped_service_name", None, None
    input_terminal = _source_session_input_terminal(request.row)
    if input_terminal is not None:
        return input_terminal
    cache_key = producer[0], request.row.native_session_ids[0]
    cached = _cached_source_session_terminal(request, cache_key)
    if cached is not None:
        return cached
    if request.row.source_kind == "metric":
        attribution = resolve_metric_attribution(
            request.connection,
            producer=producer[0],
            correlation_id=request.row.native_session_ids[0],
            source_at=request.row.source_timestamp.astimezone(UTC),
            delivery_grace_seconds=request.clock_skew_seconds,
        )
    else:
        attribution = resolve_attribution(
            request.connection,
            producer=producer[0],
            correlation_id=request.row.native_session_ids[0],
            source_at=request.row.source_timestamp.astimezone(UTC),
            clock_skew_seconds=request.clock_skew_seconds,
        )
    if attribution.state == "resolved":
        return _resolved_source_session_terminal(
            request, producer, cache_key, cast(str, attribution.evidence_id)
        )
    return _source_session_failure_terminal(request, producer, attribution.reason_code)


@dataclass(frozen=True, slots=True)
class _SourceSessionPersistenceRequest:
    connection: sqlite3.Connection
    scan_run_id: str
    rows: list[SourceSessionRow]
    persist_records: bool
    clock_skew_seconds: int


@dataclass(frozen=True, slots=True)
class _SourceSessionPersistenceState:
    current_by_key: dict[tuple[str, str, str], tuple[object, ...]]
    events: list[DerivedEvent]
    current_versions: list[tuple[object, ...]]
    current_rows: list[tuple[object, ...]]
    records: list[tuple[object, ...]]
    outcomes: dict[str, int]
    persisted_at: str
    resolved_intervals: dict[tuple[str, str], list[_ResolvedSourceSessionInterval]]


@dataclass(frozen=True, slots=True)
class _SourceSessionChangeRequest:
    persistence: _SourceSessionPersistenceRequest
    state: _SourceSessionPersistenceState
    row: SourceSessionRow
    key: tuple[str, str, str]
    projection: tuple[object, ...]
    current: tuple[object, ...] | None


def _source_session_current(
    connection: sqlite3.Connection, rows: list[SourceSessionRow]
) -> dict[tuple[str, str, str], tuple[object, ...]]:
    keys = tuple(sorted({(row.source_kind, row.service_name, row.source_id) for row in rows}))
    current_by_key: dict[tuple[str, str, str], tuple[object, ...]] = {}
    for offset in range(0, len(keys), 300):
        batch = keys[offset : offset + 300]
        placeholders = ",".join("(?, ?, ?)" for _ in batch)
        parameters = tuple(value for key in batch for value in key)
        for current in connection.execute(
            f"""SELECT source_kind, service_name, source_id, version, terminal_outcome,
            terminal_reason, context_evidence_id, project_id, project_name,
            project_root, project_kind, projection_event_id, source_timestamp_ns,
            conversation_ids_json FROM source_session_current
            WHERE (source_kind, service_name, source_id) IN ({placeholders})""",
            parameters,
        ):
            current_by_key[(str(current[0]), str(current[1]), str(current[2]))] = tuple(current[3:])
    return current_by_key


def _source_session_projection(terminal: _SourceSessionTerminal) -> tuple[object, ...]:
    outcome, reason, evidence_id, project = terminal
    return outcome, reason, evidence_id, *(project if project is not None else (None,) * 4)


def _source_session_attributes(
    row: SourceSessionRow, projection: tuple[object, ...]
) -> dict[str, str | int | float | bool]:
    outcome, reason, evidence_id, project_id, project_name, project_root, project_kind = projection
    attributes: dict[str, str | int | float | bool] = {
        "source.signal": row.source_kind,
        "source.service": row.service_name,
        "source.record.id": row.source_id,
        "source.native_key.status": row.session_status,
        "source.session_group.id": _source_session_group_id(row),
        "source.inclusion.status": "included",
        "source.inclusion.reason": "mapped_to_frozen_source_contract",
        "source.terminal.outcome": cast(str, outcome),
        "source.terminal.reason": cast(str, reason),
    }
    producer = CANONICAL_SERVICE_PRODUCERS.get(row.service_name)
    if row.session_status == "exact" and producer is not None:
        attributes.update(
            {
                "source.producer": producer[0],
                "source.producer_surface": producer[1],
                "source.session.id": row.native_session_ids[0],
            }
        )
    optional = (
        (
            "source.timestamp_ns",
            str(row.source_timestamp_ns) if row.source_timestamp_ns is not None else None,
        ),
        ("source.context.evidence_id", evidence_id),
        ("agent.project.id", project_id),
        ("agent.project.name", project_name),
        ("agent.project.root", project_root),
        ("agent.project.kind", project_kind),
    )
    for key, value in optional:
        if value is not None:
            attributes[key] = cast(str | int | float | bool, value)
    return attributes


def _source_session_identifiers(row: SourceSessionRow) -> tuple[str, str, str, str, str]:
    return (
        json.dumps(row.session_ids, separators=(",", ":")),
        json.dumps(row.thread_ids, separators=(",", ":")),
        json.dumps(row.legacy_thread_ids, separators=(",", ":")),
        json.dumps(row.gen_ai_conversation_ids, separators=(",", ":")),
        json.dumps(row.conversation_ids, separators=(",", ":")),
    )


def _append_source_session_change(request: _SourceSessionChangeRequest) -> tuple[int, str]:
    row = request.row
    version = 1 if request.current is None else int(cast(int, request.current[0])) + 1
    projection_event_id = hashlib.sha256(
        f"{_source_session_identity(row)}\x1f{version}".encode()
    ).hexdigest()
    source_timestamp_ns = _datetime_ns(row.source_timestamp)
    request.state.events.append(
        DerivedEvent(
            scope="source-session",
            event_name="introspection.source_session.recorded",
            entity_id=_source_session_identity(row),
            entity_version=version,
            event_sequence=1,
            timestamp_ns=source_timestamp_ns,
            attributes=_source_session_attributes(row, request.projection),
        )
    )
    request.state.current_versions.append(
        (
            row.source_kind,
            row.service_name,
            row.source_id,
            version,
            request.persistence.scan_run_id,
            *request.projection,
            projection_event_id,
            request.state.persisted_at,
        )
    )
    native_key = _source_native_key(row)
    request.state.current_rows.append(
        (
            row.source_kind,
            row.service_name,
            row.source_id,
            version,
            *request.projection,
            projection_event_id,
            row.source_timestamp.isoformat(),
            str(row.source_timestamp_ns) if row.source_timestamp_ns is not None else None,
            *_source_session_identifiers(row),
            *(native_key if native_key is not None else (None, None)),
            request.state.persisted_at,
        )
    )
    request.state.current_by_key[request.key] = (
        version,
        *request.projection,
        projection_event_id,
        str(row.source_timestamp_ns) if row.source_timestamp_ns is not None else None,
        json.dumps(row.conversation_ids, separators=(",", ":")),
    )
    return version, projection_event_id


def _append_source_session_record(
    request: _SourceSessionPersistenceRequest,
    state: _SourceSessionPersistenceState,
    row: SourceSessionRow,
    projection: tuple[object, ...],
    projection_event_id: str,
) -> None:
    if request.persist_records:
        state.records.append(
            (
                request.scan_run_id,
                row.source_kind,
                row.service_name,
                row.source_id,
                row.source_timestamp.isoformat(),
                str(row.source_timestamp_ns) if row.source_timestamp_ns is not None else None,
                *_source_session_identifiers(row),
                *projection,
                projection_event_id,
                state.persisted_at,
            )
        )


def _persist_source_session_row(
    request: _SourceSessionPersistenceRequest,
    state: _SourceSessionPersistenceState,
    row: SourceSessionRow,
) -> None:
    terminal = _source_session_terminal(
        _SourceSessionResolutionRequest(
            request.connection, row, request.clock_skew_seconds, state.resolved_intervals
        )
    )
    outcome = terminal[0]
    if outcome == "blocked":
        raise ScanError("observed raw source session cannot be blocked")
    key = row.source_kind, row.service_name, row.source_id
    projection = _source_session_projection(terminal)
    current = state.current_by_key.get(key)
    if (
        current is not None
        and tuple(current[1:8]) == projection
        and current[9]
        == (str(row.source_timestamp_ns) if row.source_timestamp_ns is not None else None)
        and current[10] == json.dumps(row.conversation_ids, separators=(",", ":"))
    ):
        version, projection_event_id = int(cast(int, current[0])), str(current[8])
    else:
        version, projection_event_id = _append_source_session_change(
            _SourceSessionChangeRequest(request, state, row, key, projection, current)
        )
    del version
    _append_source_session_record(request, state, row, projection, projection_event_id)
    state.outcomes["included"] += 1
    state.outcomes[outcome] += 1


def _flush_source_session_persistence(
    connection: sqlite3.Connection, state: _SourceSessionPersistenceState
) -> None:
    if state.events:
        enqueue_events(connection, state.events)
    if state.current_versions:
        connection.executemany(
            """
            INSERT INTO source_session_current_versions (
                source_kind, service_name, source_id, version, scan_run_id, terminal_outcome,
                terminal_reason, context_evidence_id, project_id, project_name, project_root,
                project_kind, projection_event_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            state.current_versions,
        )
        connection.executemany(
            """
            INSERT INTO source_session_current (
                source_kind, service_name, source_id, version, terminal_outcome, terminal_reason,
                context_evidence_id, project_id, project_name, project_root, project_kind,
                projection_event_id, source_timestamp, source_timestamp_ns,
                session_ids_json, thread_ids_json, legacy_thread_ids_json,
                gen_ai_conversation_ids_json, conversation_ids_json,
                native_producer, native_session_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_kind, service_name, source_id) DO UPDATE SET
                version = excluded.version, terminal_outcome = excluded.terminal_outcome,
                terminal_reason = excluded.terminal_reason,
                context_evidence_id = excluded.context_evidence_id,
                project_id = excluded.project_id, project_name = excluded.project_name,
                project_root = excluded.project_root, project_kind = excluded.project_kind,
                projection_event_id = excluded.projection_event_id,
                source_timestamp = excluded.source_timestamp,
                source_timestamp_ns = excluded.source_timestamp_ns,
                conversation_ids_json = excluded.conversation_ids_json,
                session_ids_json = excluded.session_ids_json,
                thread_ids_json = excluded.thread_ids_json,
                legacy_thread_ids_json = excluded.legacy_thread_ids_json,
                gen_ai_conversation_ids_json = excluded.gen_ai_conversation_ids_json,
                native_producer = excluded.native_producer,
                native_session_id = excluded.native_session_id,
                updated_at = excluded.updated_at
            """,
            state.current_rows,
        )
    if state.records:
        connection.executemany(
            """
            INSERT INTO source_session_records (
                scan_run_id, source_kind, service_name, source_id, source_timestamp,
                source_timestamp_ns, session_ids_json, thread_ids_json, legacy_thread_ids_json,
                gen_ai_conversation_ids_json, conversation_ids_json, terminal_outcome,
                terminal_reason,
                context_evidence_id, project_id, project_name, project_root, project_kind,
                projection_event_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            state.records,
        )


def _persist_source_sessions(request: _SourceSessionPersistenceRequest) -> dict[str, int]:
    """Append raw history and version the canonical current projection only when it changes."""
    outcomes = {"included": 0, "attributed": 0, "expected_rejection": 0, "failed": 0, "blocked": 0}
    state = _SourceSessionPersistenceState(
        _source_session_current(request.connection, request.rows),
        [],
        [],
        [],
        [],
        outcomes,
        _iso_now(),
        {},
    )
    for row in request.rows:
        _persist_source_session_row(request, state, row)
    _flush_source_session_persistence(request.connection, state)
    if outcomes["blocked"] != 0:
        raise ScanError("observed raw source sessions cannot be blocked")
    if outcomes["included"] != sum(
        outcomes[name] for name in ("attributed", "expected_rejection", "failed")
    ):
        raise ScanError("raw source terminal outcomes do not conserve the included population")
    return outcomes


def _complete_raw_source_window(
    connection: sqlite3.Connection, *, start_ns: int, end_ns: int
) -> None:
    """Record completion and advance the raw watermark in the caller's transaction."""
    connection.execute(
        """
        INSERT INTO raw_source_window_completions (source, start_ns, end_ns, completed_at)
        VALUES ('signoz_raw_source_sessions', ?, ?, ?)
        """,
        (start_ns, end_ns, _iso_now()),
    )
    _advance_raw_source_watermark(connection, end_ns=end_ns)


def _advance_raw_source_watermark(connection: sqlite3.Connection, *, end_ns: int) -> None:
    """Advance the half-open raw-source boundary only inside a successful scan."""
    connection.execute(
        """
        INSERT INTO source_watermarks (source, timestamp_ns, row_id, updated_at)
        VALUES ('signoz_raw_source_sessions', ?, '', ?)
        ON CONFLICT(source) DO UPDATE SET
            timestamp_ns = excluded.timestamp_ns,
            row_id = excluded.row_id,
            updated_at = excluded.updated_at
        WHERE (excluded.timestamp_ns, excluded.row_id)
            > (source_watermarks.timestamp_ns, source_watermarks.row_id)
        """,
        (end_ns, _iso_now()),
    )


def _advance_activity_source_watermark(connection: sqlite3.Connection, *, end_ns: int) -> None:
    """Advance the detector source cursor only with a successful extraction commit."""
    connection.execute(
        """
        INSERT INTO source_watermarks (source, timestamp_ns, row_id, updated_at)
        VALUES ('signoz_logs', ?, '', ?)
        ON CONFLICT(source) DO UPDATE SET
            timestamp_ns = excluded.timestamp_ns,
            row_id = excluded.row_id,
            updated_at = excluded.updated_at
        WHERE excluded.timestamp_ns > source_watermarks.timestamp_ns
        """,
        (end_ns, _iso_now()),
    )


def _canonical_activity(
    observation: Observation,
    event_index: dict[str, DetectorEvent],
    logs_by_id: dict[str, LogRow],
    traces_by_id: dict[str, TraceRow],
) -> CanonicalActivity:
    event_ids = tuple(sorted(set(observation.event_ids)))
    correlations = []
    log_ids: set[str] = set()
    span_ids: set[str] = set()
    for event_id in event_ids:
        if event_id.startswith("trace:"):
            trace = traces_by_id.get(event_id.removeprefix("trace:"))
            if trace is None or trace.correlation is None:
                raise ScanError(
                    f"observation {observation.fingerprint} lacks canonical trace correlation"
                )
            correlations.append(trace.correlation)
            span_ids.update(trace.correlation.source_span_ids)
            continue
        log = logs_by_id.get(event_id)
        trace = traces_by_id.get(log.trace_id or "") if log is not None else None
        if log is None or trace is None or trace.correlation is None:
            raise ScanError(
                f"observation {observation.fingerprint} lacks canonical log correlation"
            )
        correlations.append(trace.correlation)
        log_ids.add(log.log_id)
        if log.span_id is not None:
            span_ids.add(log.span_id)
    producer_keys = {
        (item.producer, item.producer_surface, item.correlation_id) for item in correlations
    }
    if len(producer_keys) != 1:
        raise ScanError(
            f"observation {observation.fingerprint} has ambiguous canonical correlation"
        )
    producer, producer_surface, correlation_id = producer_keys.pop()
    components = observation.fingerprint_components
    timestamps = [
        int(event_index[event_id].timestamp.timestamp() * 1_000_000_000) for event_id in event_ids
    ]
    return CanonicalActivity(
        producer=producer,
        producer_surface=producer_surface,
        correlation_id=correlation_id,
        source_started_at_ns=min(timestamps),
        source_ended_at_ns=max(timestamps),
        detector_id=observation.detector_id,
        detector_version=observation.detector_version,
        normalization_version=1,
        source_membership=CanonicalSourceMembership(
            event_ids=event_ids,
            log_ids=tuple(log_ids),
            span_ids=tuple(span_ids),
        ),
        operation_kind=components.operation_kind,
        target_kind=components.target_kind,
        normalized_target=components.normalized_target,
        normalized_failure_class=components.normalized_failure_class,
        created_at=datetime.fromtimestamp(max(timestamps) / 1_000_000_000, tz=UTC).isoformat(),
    )


def _project_from_attribution_evidence(
    connection: sqlite3.Connection, attribution: CanonicalAttribution
) -> ProjectIdentity:
    """Load the immutable project tuple selected by the central resolver."""
    if attribution.project_identity_id is None or attribution.evidence_id is None:
        raise ScanError("resolved canonical attribution lacks context evidence")
    if attribution.method == "session_context_interval":
        row = connection.execute(
            """
            SELECT project_id, project_name, project_root, project_kind
            FROM session_context_intervals
            WHERE event_id = ? AND project_id = ?
            """,
            (attribution.evidence_id, attribution.project_identity_id),
        ).fetchone()
    elif attribution.method == "session_context":
        row = connection.execute(
            """
            SELECT project_id, project_name, project_root, project_kind
            FROM session_context_events
            WHERE event_id = ? AND producer = 'codex-cli'
              AND event_type = 'session_context' AND project_id = ?
            """,
            (attribution.evidence_id, attribution.project_identity_id),
        ).fetchone()
    else:
        raise ScanError("resolved canonical attribution has an unsupported context method")
    if row is None:
        raise ScanError("resolved canonical attribution lacks a context project identity")
    return ProjectIdentity(str(row[3]), Path(str(row[2])), str(row[0]), str(row[1]))


def _persist_context_projects(
    connection: sqlite3.Connection, context_events: tuple[DerivedEvent, ...]
) -> None:
    """Persist approved context project tuples before late reconciliation."""
    event_ids = tuple(event.entity_id for event in context_events)
    if not event_ids:
        return
    rows = connection.execute(
        """
        SELECT project_id, project_name, project_root, project_kind
        FROM session_context_intervals
        WHERE event_id IN ({placeholders})
        UNION ALL
        SELECT project_id, project_name, project_root, project_kind
        FROM session_context_events
        WHERE event_id IN ({placeholders}) AND producer = 'codex-cli'
          AND event_type = 'session_context'
        """.format(placeholders=",".join("?" for _ in event_ids)),
        (*event_ids, *event_ids),
    ).fetchall()
    _persist_projects(
        connection,
        {
            str(row[0]): ProjectIdentity(str(row[3]), Path(str(row[2])), str(row[0]), str(row[1]))
            for row in rows
        },
    )


def _persist_attribution_project(
    connection: sqlite3.Connection,
    attribution: CanonicalAttribution,
) -> None:
    if attribution.project_identity_id is None:
        return
    project = _project_from_attribution_evidence(connection, attribution)
    _persist_projects(connection, {project.identity: project})


def _persist_canonical_activities(
    connection: sqlite3.Connection,
    activities: list[CanonicalActivity],
    *,
    now: datetime,
) -> tuple[list[str], list[str], list[TrendEvaluation]]:
    persisted_ids: list[str] = []
    changed_ids: list[str] = []
    for activity in activities:
        source_at = datetime.fromtimestamp(activity.source_ended_at_ns / 1_000_000_000, tz=UTC)
        attribution = resolve_attribution(
            connection,
            producer=activity.producer,
            correlation_id=activity.correlation_id,
            source_at=source_at,
        ).canonical(created_at=_iso_now())
        _persist_attribution_project(connection, attribution)
        write = persist_canonical_activity(connection, activity, attribution)
        persisted_ids.append(write.activity_id)
        attributes = canonical_activity_event_attributes(connection, activity, attribution)
        enqueue_canonical_activity_version(
            connection,
            CanonicalActivityVersionEvent(
                activity_id=write.activity_id,
                version=write.version,
                timestamp_ns=activity.source_ended_at_ns,
                attributes=attributes,
            ),
        )
        if not write.version_inserted:
            continue
        connection.executemany(
            """
            INSERT INTO canonical_recomputation_schedule (
                activity_id, activity_version, aggregate_kind, scheduled_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(activity_id, activity_version, aggregate_kind) DO NOTHING
            """,
            [
                (write.activity_id, write.version, "findings", _iso_now()),
                (write.activity_id, write.version, "trends", _iso_now()),
            ],
        )
        changed_ids.append(write.activity_id)
    evaluations = recompute_canonical_findings(connection, changed_ids, now=now)
    if changed_ids:
        connection.execute(
            """
            UPDATE canonical_recomputation_schedule
            SET completed_at = ?
            WHERE activity_id IN ({}) AND completed_at IS NULL
            """.format(",".join("?" for _ in changed_ids)),
            (_iso_now(), *changed_ids),
        )
    return persisted_ids, changed_ids, evaluations


def _canonical_activity_from_storage(row: tuple[Any, ...]) -> CanonicalActivity:
    membership = json.loads(str(row[8]))
    return CanonicalActivity(
        producer=str(row[0]),
        producer_surface=str(row[1]),
        correlation_id=str(row[2]),
        source_started_at_ns=int(row[3]),
        source_ended_at_ns=int(row[4]),
        detector_id=str(row[5]),
        detector_version=int(row[6]),
        normalization_version=int(row[7]),
        source_membership=CanonicalSourceMembership(
            event_ids=tuple(membership["event_ids"]),
            log_ids=tuple(membership["log_ids"]),
            span_ids=tuple(membership["span_ids"]),
        ),
        operation_kind=str(row[9]),
        target_kind=str(row[10]),
        normalized_target=str(row[11]),
        normalized_failure_class=str(row[12]),
        created_at=str(row[13]),
    )


def _ensure_current_activity_outbox(connection: sqlite3.Connection) -> None:
    """Ensure every latest canonical activity version has the current OTLP projection."""
    rows = connection.execute(
        """
        SELECT a.producer, a.producer_surface, a.correlation_id, a.source_started_at_ns,
               a.source_ended_at_ns, a.detector_id, a.detector_version,
               a.normalization_version, a.source_membership_json, a.operation_kind,
               a.target_kind, a.normalized_target, a.normalized_failure_class, a.created_at,
               av.version, av.attribution_state, av.project_identity_id,
               av.attribution_method, av.attribution_evidence_id, av.reason_code, av.created_at
        FROM canonical_activities AS a
        JOIN canonical_activity_versions AS av ON av.activity_id = a.id
        WHERE av.version = (
          SELECT MAX(latest.version)
          FROM canonical_activity_versions AS latest
          WHERE latest.activity_id = a.id
        )
        """
    ).fetchall()
    for row in rows:
        activity = _canonical_activity_from_storage(row)
        attribution = CanonicalAttribution(
            state=str(row[15]),
            project_identity_id=str(row[16]) if row[16] is not None else None,
            method=str(row[17]),
            evidence_id=str(row[18]) if row[18] is not None else None,
            reason_code=str(row[19]) if row[19] is not None else None,
            created_at=str(row[20]),
        )
        enqueue_canonical_activity_version(
            connection,
            CanonicalActivityVersionEvent(
                activity_id=activity.id,
                version=int(row[14]),
                timestamp_ns=activity.source_ended_at_ns,
                attributes=canonical_activity_event_attributes(connection, activity, attribution),
            ),
        )


def _reconcile_late_source_sessions(
    connection: sqlite3.Connection,
    *,
    scan_run_id: str,
    clock_skew_seconds: int = 0,
) -> dict[str, int]:
    """Reproject every exact current raw row and close durable obligations."""
    pending = connection.execute(
        """
        SELECT producer, session_id
        FROM source_session_reconciliation_pending
        WHERE completed_at IS NULL
        ORDER BY producer, session_id
        """
    ).fetchall()
    rows_to_reconcile: list[SourceSessionRow] = []
    row_identities: dict[tuple[str, str], set[tuple[str, str, str]]] = {}
    seen: set[tuple[str, str, str]] = set()
    for producer_value, session_value in pending:
        producer = str(producer_value)
        session_id = str(session_value)
        row_identities.setdefault((producer, session_id), set())
        if producer not in {
            canonical_producer[0] for canonical_producer in CANONICAL_SERVICE_PRODUCERS.values()
        }:
            raise ScanError("pending raw reconciliation has unknown producer")
        rows = connection.execute(
            """
            SELECT source_kind, source_id, source_timestamp, service_name,
                   session_ids_json, thread_ids_json, legacy_thread_ids_json,
                   gen_ai_conversation_ids_json, conversation_ids_json, source_timestamp_ns
            FROM source_session_current
            WHERE native_producer = ? AND native_session_id = ?
            """,
            (producer, session_id),
        ).fetchall()
        for raw in rows:
            source_kind = str(raw[0])
            if source_kind not in ("log", "trace", "metric"):
                raise ScanError("current raw source session has invalid source kind")
            if any(value is None for value in raw[2:8]):
                raise ScanError("current raw source session lacks durable native-key payload")
            row = SourceSessionRow(
                source_kind=cast(Literal["log", "trace", "metric"], source_kind),
                source_id=str(raw[1]),
                source_timestamp=datetime.fromisoformat(str(raw[2])).astimezone(UTC),
                service_name=str(raw[3]),
                session_ids=tuple(json.loads(str(raw[4]))),
                thread_ids=tuple(json.loads(str(raw[5]))),
                legacy_thread_ids=tuple(json.loads(str(raw[6]))),
                gen_ai_conversation_ids=tuple(json.loads(str(raw[7]))),
                conversation_ids=tuple(json.loads(str(raw[8]))) if raw[8] is not None else (),
                source_timestamp_ns=int(str(raw[9])) if raw[9] is not None else None,
            )
            if row.session_status != "exact" or row.native_session_ids[0] != session_id:
                continue
            identity = (row.source_kind, row.service_name, row.source_id)
            row_identities.setdefault((producer, session_id), set()).add(identity)
            if identity not in seen:
                seen.add(identity)
                rows_to_reconcile.append(row)
    outcomes = _persist_source_sessions(
        _SourceSessionPersistenceRequest(
            connection,
            scan_run_id,
            rows_to_reconcile,
            persist_records=False,
            clock_skew_seconds=clock_skew_seconds,
        )
    )
    completed_at = _iso_now()
    for pending_identity, source_identities in row_identities.items():
        if source_identities and all(
            (
                current := connection.execute(
                    """
                    SELECT terminal_outcome, version
                    FROM source_session_current
                    WHERE source_kind = ? AND service_name = ? AND source_id = ?
                    """,
                    source_identity,
                ).fetchone()
            )
            is not None
            and str(current[0]) != "blocked"
            and connection.execute(
                "SELECT 1 FROM otlp_outbox WHERE event_id = ?",
                (
                    DerivedEvent(
                        scope="source-session",
                        event_name="introspection.source_session.recorded",
                        entity_id=hashlib.sha256(
                            json.dumps(source_identity, separators=(",", ":")).encode()
                        ).hexdigest(),
                        entity_version=int(current[1]),
                        event_sequence=1,
                        timestamp_ns=0,
                        attributes={},
                    ).event_id,
                ),
            ).fetchone()
            is not None
            for source_identity in source_identities
        ):
            connection.execute(
                """
                UPDATE source_session_reconciliation_pending
                SET completed_at = ?
                WHERE producer = ? AND session_id = ? AND completed_at IS NULL
                """,
                (completed_at, *pending_identity),
            )
    return outcomes


def _reconcile_late_context(
    connection: sqlite3.Connection, context_events: tuple[DerivedEvent, ...]
) -> list[str]:
    changed_ids: list[str] = []
    seen: set[tuple[str, str]] = set()
    for event in context_events:
        producer = event.attributes.get("producer")
        correlation_id = event.attributes.get("session.id")
        if not isinstance(producer, str) or not isinstance(correlation_id, str):
            continue
        if (producer, correlation_id) in seen:
            continue
        seen.add((producer, correlation_id))
        rows = connection.execute(
            """
            SELECT producer, producer_surface, correlation_id, source_started_at_ns,
                   source_ended_at_ns, detector_id, detector_version, normalization_version,
                   source_membership_json, operation_kind, target_kind, normalized_target,
                   normalized_failure_class, created_at
            FROM canonical_activities
            WHERE producer = ? AND correlation_id = ?
            """,
            (producer, correlation_id),
        ).fetchall()
        for row in rows:
            activity = _canonical_activity_from_storage(row)
            source_at = datetime.fromtimestamp(activity.source_ended_at_ns / 1_000_000_000, tz=UTC)
            write = reconcile_activity(connection, activity=activity, source_at=source_at)
            if write.version_inserted:
                changed_ids.append(write.activity_id)
    return changed_ids


def _persist_projects(connection: sqlite3.Connection, projects: dict[str, ProjectIdentity]) -> None:
    now = _iso_now()
    for project in projects.values():
        connection.execute(
            """
            INSERT INTO project_identities (
                id, identity_kind, canonical_path, git_common_dir, canonical_name, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET canonical_name = excluded.canonical_name
            WHERE project_identities.canonical_name IS NULL
            """,
            (
                project.identity,
                project.kind,
                project.root.as_posix(),
                (project.root / ".git").as_posix() if project.kind == "git" else None,
                project.display_name,
                now,
            ),
        )


@dataclass(frozen=True, slots=True)
class _ScanWindow:
    start_ns: int
    end_ns: int
    start_bucket: int
    end_bucket: int
    raw_source_window: tuple[int, int] | None


@dataclass(frozen=True, slots=True)
class _SourceAcquisition:
    logs: list[LogRow]
    traces: list[TraceRow]
    hydration: list[HydrationRow]
    source_sessions: list[SourceSessionRow]
    logs_stream: PipelineStream
    traces_stream: PipelineStream
    hydration_stream: PipelineStream


@dataclass(frozen=True, slots=True)
class _DetectorPersistence:
    activities: list[CanonicalActivity]
    canonical_activity_ids: list[str]
    trend_evaluations: list[TrendEvaluation]
    source_conservation: dict[str, int]


@dataclass(frozen=True, slots=True)
class RecurrenceFindingScanObservation:
    """One committed canonical finding/member snapshot for an experiment observer."""

    scan_run_id: str
    observed_at: datetime
    evidence_start_ns: int | None
    evidence_end_ns: int | None
    evidence_window_definition: str | None
    finding_version_source_id: str
    membership_source_id: str
    activity_version_source_id: str
    activity_source_started_at_ns: int
    activity_source_ended_at_ns: int
    detector_id: str
    detector_version: int
    finding_fingerprint: str
    finding_state: str
    finding_occurrence_count: int
    finding_canonical_task_count: int
    finding_local_day_count: int
    task_membership_state: str
    native_task_id: str | None
    producer: str
    activity_version: int
    latest_activity_version: int

    def __post_init__(self) -> None:
        if not self.scan_run_id or not self.detector_id or not self.finding_state:
            raise ValueError("scan observation identity is required")
        if (
            not self.producer
            or type(self.activity_version) is not int
            or type(self.latest_activity_version) is not int
            or self.activity_version < 1
            or self.latest_activity_version < 1
        ):
            raise ValueError("scan observation requires exact activity versions and producer")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("scan observation time must be timezone-aware")
        for value, name in (
            (self.finding_version_source_id, "finding version source ID"),
            (self.membership_source_id, "membership source ID"),
            (self.activity_version_source_id, "activity version source ID"),
            (self.finding_fingerprint, "finding fingerprint"),
        ):
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ValueError(f"{name} must be a SHA-256 digest")
        self._validate_native_membership()
        self._validate_evaluation_window()
        for numeric_value, name in (
            (self.activity_source_started_at_ns, "activity source start"),
            (self.activity_source_ended_at_ns, "activity source end"),
            (self.detector_version, "detector version"),
            (self.finding_occurrence_count, "finding occurrence count"),
            (self.finding_canonical_task_count, "finding canonical task count"),
            (self.finding_local_day_count, "finding local day count"),
        ):
            if (
                isinstance(numeric_value, bool)
                or not isinstance(numeric_value, int)
                or numeric_value < 0
            ):
                raise ValueError(f"{name} must be a nonnegative integer")

    def _validate_native_membership(self) -> None:
        if self.native_task_id is not None and (
            not self.native_task_id.startswith("thread:") or self.native_task_id == "thread:"
        ):
            raise ValueError("native task ID must retain an exact thread identity")
        if self.task_membership_state not in (
            "qualified",
            "episode_task_identity",
            "multiple_canonical_tasks",
            "missing_detector_event",
            "historical_detector_event",
            "unbound_task_identity",
        ):
            raise ValueError("task membership state is not registered")
        if (self.task_membership_state == "qualified") != (self.native_task_id is not None):
            raise ValueError("qualified membership requires exactly one native task ID")

    def _validate_evaluation_window(self) -> None:
        if (self.evidence_start_ns is None) != (self.evidence_end_ns is None):
            raise ValueError("finding evaluation bounds must be observed together")
        if (self.evidence_start_ns is None) != (self.evidence_window_definition is None):
            raise ValueError("finding evaluation window definition must be observed with bounds")
        if self.evidence_window_definition not in (
            None,
            "rolling_utc_7d",
            "europe_london_calendar_7d",
        ):
            raise ValueError("finding evaluation window definition is not registered")


@dataclass(frozen=True, slots=True)
class _DetectorPersistenceRequest:
    connection: sqlite3.Connection
    config: AppConfig
    scan_run_id: str
    now: datetime
    window: _ScanWindow
    sources: _SourceAcquisition
    experiment_observer: Callable[[tuple[RecurrenceFindingScanObservation, ...]], None] | None


@dataclass(frozen=True, slots=True)
class _LeasedScanRequest:
    connection: sqlite3.Connection
    config: AppConfig
    source: ClickHouseClient
    now: datetime
    scan_run_id: str
    started: float
    deadline: tuple[Any, tuple[float, float]]
    experiment_observer: Callable[[tuple[RecurrenceFindingScanObservation, ...]], None] | None


def _prepare_scan_window(
    connection: sqlite3.Connection,
    *,
    config: AppConfig,
    source: ClickHouseClient,
    end_ns: int,
) -> _ScanWindow:
    verify_network_perimeter(docker_context=config.signoz.docker_context)
    enforce_approved_schema(connection, discover_source_schema(source))
    try:
        initial_start_ns = _raw_source_anchor(connection)
    except ScanError:
        logs_earliest_ns, traces_earliest_ns = source.raw_source_window_anchor()
        _approve_raw_source_anchor(
            connection,
            logs_earliest_ns=logs_earliest_ns,
            traces_earliest_ns=traces_earliest_ns,
        )
        initial_start_ns = _raw_source_anchor(connection)
    start_ns, bounded_end_ns, start_bucket, end_bucket = _bounds(
        connection,
        end_ns,
        initial_start_ns=initial_start_ns,
        replay_overlap_seconds=config.lifecycle.clock_skew_seconds,
    )
    return _ScanWindow(
        start_ns=start_ns,
        end_ns=bounded_end_ns,
        start_bucket=start_bucket,
        end_bucket=end_bucket,
        raw_source_window=_claim_raw_source_window(connection, end_ns=bounded_end_ns),
    )


def _acquire_scan_sources(
    source: ClickHouseClient, window: _ScanWindow, sources: _SourceAcquisition
) -> _SourceAcquisition:
    sources.logs.extend(source.logs(start_ns=window.start_ns, end_ns=window.end_ns))
    sources.logs_stream.query_status = "available"
    sources.logs_stream.data_state = "records" if sources.logs else "no_data"
    sources.logs_stream.latest_timestamp_ns = max(
        (log.timestamp_ns for log in sources.logs), default=None
    )
    if window.raw_source_window is not None:
        raw_start_ns, raw_end_ns = window.raw_source_window
        sources.source_sessions.extend(
            source.source_sessions(
                start=datetime.fromtimestamp(raw_start_ns / 1_000_000_000, tz=UTC),
                end=datetime.fromtimestamp(raw_end_ns / 1_000_000_000, tz=UTC),
                start_ns=raw_start_ns,
                end_ns=raw_end_ns,
            )
        )
    sources.traces.extend(
        source.traces(
            start=datetime.fromtimestamp(window.start_ns / 1_000_000_000, tz=UTC),
            end=datetime.fromtimestamp(window.end_ns / 1_000_000_000, tz=UTC),
        )
    )
    sources.traces_stream.query_status = "available"
    sources.traces_stream.data_state = "records" if sources.traces else "no_data"
    sources.traces_stream.latest_timestamp_ns = max(
        (int(trace.ended_at.timestamp() * 1_000_000_000) for trace in sources.traces),
        default=None,
    )
    shortlisted = _shortlisted_log_ids(
        sources.logs, {trace.trace_id: trace for trace in sources.traces}
    )
    for offset in range(0, len(shortlisted), 250):
        sources.hydration.extend(
            source.hydrate(
                HydrationRequest(
                    identity_kind="log_id",
                    identifiers=shortlisted[offset : offset + 250],
                    start_ns=window.start_ns,
                    end_ns=window.end_ns,
                    start_bucket=window.start_bucket,
                    end_bucket=window.end_bucket,
                )
            )
        )
    sources.hydration_stream.query_status = "available"
    sources.hydration_stream.data_state = "records" if sources.hydration else "no_data"
    return sources


def _source_hash(*values: object) -> str:
    return hashlib.sha256(
        json.dumps(values, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _task_membership_authority(
    *,
    event_ids: tuple[str, ...],
    event_index: dict[str, DetectorEvent],
    activity_source_ended_at_ns: int,
    window: _ScanWindow,
) -> tuple[str, str | None]:
    events = tuple(event_index[event_id] for event_id in event_ids if event_id in event_index)
    if len(events) != len(event_ids):
        state = (
            "historical_detector_event"
            if activity_source_ended_at_ns < window.start_ns
            else "missing_detector_event"
        )
        return state, None
    task_ids = {event.task_id for event in events}
    if any(
        not event.counts_as_distinct_task or event.task_id.startswith("episode:")
        for event in events
    ):
        return "episode_task_identity", None
    if any(not event.task_id.startswith("thread:") for event in events):
        return "unbound_task_identity", None
    if len(task_ids) != 1:
        return "multiple_canonical_tasks", None
    return "qualified", task_ids.pop()


def _recurrence_finding_observations(
    request: _DetectorPersistenceRequest,
    evaluations: list[TrendEvaluation],
    event_index: dict[str, DetectorEvent],
) -> tuple[RecurrenceFindingScanObservation, ...]:
    """Freeze active finding memberships from the recomputation transaction."""
    evaluated = {evaluation.finding_id: evaluation for evaluation in evaluations}
    connection = request.connection
    rows = connection.execute(
        """
        SELECT f.id, f.entity_version, f.fingerprint, f.detector_id, f.detector_version,
               f.trend_state, f.occurrence_count, f.canonical_task_count, f.local_day_count,
               cfm.activity_id, cfm.rationale, cfm.created_at,
               a.source_ended_at_ns, a.source_membership_json,
               av.version,
               (SELECT MAX(latest.version)
                FROM canonical_activity_versions latest
                WHERE latest.activity_id = a.id) AS latest_activity_version,
               a.source_started_at_ns, a.producer
        FROM findings f
        JOIN canonical_finding_membership cfm ON cfm.finding_id = f.id
        JOIN canonical_activities a ON a.id = cfm.activity_id
        JOIN canonical_activity_versions av
          ON av.activity_id = a.id
         AND av.version = (
             SELECT MAX(latest.version)
             FROM canonical_activity_versions latest
             WHERE latest.activity_id = a.id
         )
        WHERE f.is_active = 1
        ORDER BY f.id, cfm.activity_id
        """
    ).fetchall()
    frozen: list[RecurrenceFindingScanObservation] = []
    for row in rows:
        membership = json.loads(str(row[13]))
        event_ids = tuple(sorted(str(event_id) for event_id in membership["event_ids"]))
        activity_source_ended_at_ns = int(row[12])
        task_membership_state, native_task_id = _task_membership_authority(
            event_ids=event_ids,
            event_index=event_index,
            activity_source_ended_at_ns=activity_source_ended_at_ns,
            window=request.window,
        )
        finding_id = str(row[0])
        evaluation = evaluated.get(finding_id)
        activity_id = str(row[9])
        frozen.append(
            RecurrenceFindingScanObservation(
                scan_run_id=request.scan_run_id,
                observed_at=request.now.astimezone(UTC),
                evidence_start_ns=evaluation.window_started_at_ns if evaluation else None,
                evidence_end_ns=evaluation.window_ended_at_ns if evaluation else None,
                evidence_window_definition="rolling_utc_7d" if evaluation else None,
                finding_version_source_id=_source_hash(
                    "finding",
                    finding_id,
                    int(row[1]),
                    str(row[2]),
                    str(row[5]),
                    int(row[6]),
                    int(row[7]),
                    int(row[8]),
                ),
                membership_source_id=_source_hash(
                    "finding_membership",
                    finding_id,
                    activity_id,
                    str(row[10]),
                    str(row[11]),
                ),
                activity_version_source_id=_source_hash(
                    "activity_version", activity_id, int(row[14])
                ),
                activity_source_started_at_ns=int(row[16]),
                activity_source_ended_at_ns=activity_source_ended_at_ns,
                finding_fingerprint=str(row[2]),
                detector_id=str(row[3]),
                detector_version=int(row[4]),
                finding_state=str(row[5]),
                finding_occurrence_count=int(row[6]),
                finding_canonical_task_count=int(row[7]),
                finding_local_day_count=int(row[8]),
                task_membership_state=task_membership_state,
                native_task_id=native_task_id,
                producer=str(row[17]),
                activity_version=int(row[14]),
                latest_activity_version=int(row[15]),
            )
        )
    return tuple(frozen)


def _detect_and_persist(request: _DetectorPersistenceRequest) -> _DetectorPersistence:
    connection = request.connection
    config = request.config
    scan_run_id = request.scan_run_id
    now = request.now
    window = request.window
    sources = request.sources
    events = _detector_events(sources.logs, sources.traces, sources.hydration)
    token_baselines: dict[str, list[int]] = defaultdict(list)
    for event in events:
        if event.token_count is not None:
            token_baselines[event.project_id].append(event.token_count)
    event_index = {event.event_id: event for event in events}
    logs_by_id = {log.log_id: log for log in sources.logs}
    traces_by_id = {trace.trace_id: trace for trace in sources.traces}
    observations = _partition_observations_by_context(
        connection,
        DetectorEngine().detect(events, token_baselines=token_baselines),
        event_index,
        traces_by_id,
        logs_by_id,
    )
    activities = [
        _canonical_activity(observation, event_index, logs_by_id, traces_by_id)
        for observation in observations
    ]
    connection.execute("BEGIN IMMEDIATE")
    _persist_source_rejections(connection, sources.traces)
    canonical_activity_ids, _, trend_evaluations = _persist_canonical_activities(
        connection, activities, now=now
    )
    source_conservation = _persist_source_sessions(
        _SourceSessionPersistenceRequest(
            connection,
            scan_run_id,
            sources.source_sessions,
            persist_records=True,
            clock_skew_seconds=config.lifecycle.clock_skew_seconds,
        )
    )
    _reconcile_late_source_sessions(
        connection,
        scan_run_id=scan_run_id,
        clock_skew_seconds=config.lifecycle.clock_skew_seconds,
    )
    if window.raw_source_window is not None:
        _complete_raw_source_window(
            connection,
            start_ns=window.raw_source_window[0],
            end_ns=window.raw_source_window[1],
        )
    _advance_activity_source_watermark(connection, end_ns=window.end_ns)
    _ensure_current_activity_outbox(connection)
    frozen_observations = (
        _recurrence_finding_observations(
            request,
            trend_evaluations,
            event_index=event_index,
        )
        if request.experiment_observer is not None
        else ()
    )
    connection.commit()
    if request.experiment_observer is not None:
        request.experiment_observer(frozen_observations)
    return _DetectorPersistence(
        activities, canonical_activity_ids, trend_evaluations, source_conservation
    )


def _scan_details(
    sources: _SourceAcquisition,
    persistence: _DetectorPersistence,
    context_events: tuple[DerivedEvent, ...],
) -> str:
    return json.dumps(
        {
            "hydrated": len(sources.hydration),
            "logs": len(sources.logs),
            "canonical_activities": len(persistence.activities),
            "traces": len(sources.traces),
            "trends": len(persistence.trend_evaluations),
            "session_context_events": len(context_events),
            "source_sessions": len(sources.source_sessions),
            "source_conservation": persistence.source_conservation,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def run_scan(
    connection: sqlite3.Connection,
    config: AppConfig,
    *,
    client: ClickHouseClient | None = None,
    end_time: datetime | None = None,
    experiment_observer: Callable[[tuple[RecurrenceFindingScanObservation, ...]], None]
    | None = None,
) -> dict[str, Any]:
    """Run one fail-closed canonical extraction and reconciliation window."""
    started = time.monotonic()
    now = end_time or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("scan end_time must be timezone-aware")
    source = client or ClickHouseClient(
        docker_context=config.signoz.docker_context,
        container=config.signoz.clickhouse_container,
    )
    scan_run_id = str(uuid.uuid4())
    deadline = _arm_scan_deadline()
    try:
        lease = scheduler.acquire_lease(
            connection, duration=timedelta(seconds=config.scheduler.lease_seconds)
        )
    except BaseException:
        _disarm_scan_deadline(deadline)
        raise
    try:
        return _run_leased_scan(
            _LeasedScanRequest(
                connection,
                config,
                source,
                now,
                scan_run_id,
                started,
                deadline,
                experiment_observer,
            )
        )
    finally:
        scheduler.release_lease(connection, lease)


def _begin_scan_run(request: _LeasedScanRequest, snapshot_end_ns: int) -> None:
    with request.connection:
        request.connection.execute(
            """
            INSERT INTO scan_runs (
                id, status, started_at, source_end_ns, details_json
            ) VALUES (?, 'running', ?, ?, '{}')
            """,
            (request.scan_run_id, _iso_now(), snapshot_end_ns),
        )


def _process_context_phase(
    request: _LeasedScanRequest, persistence: _DetectorPersistence
) -> tuple[tuple[DerivedEvent, ...], _DetectorPersistence]:
    connection = request.connection
    context_events = drain_inbox(connection, directory=inbox_path(request.config.database.path))
    with connection:
        enqueue_events(connection, list(context_events))
        _persist_context_projects(connection, context_events)
    late_activity_ids = _reconcile_late_context(connection, context_events)
    if not late_activity_ids:
        return context_events, persistence
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        persistence = replace(
            persistence,
            trend_evaluations=recompute_canonical_findings(
                connection, late_activity_ids, now=request.now
            ),
        )
        connection.execute(
            """
            UPDATE canonical_recomputation_schedule
            SET completed_at = ?
            WHERE activity_id IN ({}) AND completed_at IS NULL
            """.format(",".join("?" for _ in late_activity_ids)),
            (_iso_now(), *late_activity_ids),
        )
    return context_events, persistence


def _process_source_phase(
    request: _LeasedScanRequest,
    window: _ScanWindow,
    sources: _SourceAcquisition,
    persistence: _DetectorPersistence,
) -> tuple[_SourceAcquisition, _DetectorPersistence, str]:
    sources = _acquire_scan_sources(request.source, window, sources)
    current_persistence = _detect_and_persist(
        _DetectorPersistenceRequest(
            request.connection,
            request.config,
            request.scan_run_id,
            request.now,
            window,
            sources,
            request.experiment_observer,
        )
    )
    persistence = replace(
        current_persistence,
        trend_evaluations=(persistence.trend_evaluations + current_persistence.trend_evaluations),
    )
    terminal_status = "no_data" if not sources.logs and not sources.traces else "succeeded"
    return sources, persistence, terminal_status


def _contains_scan_deadline(error: BaseException, seen: set[int] | None = None) -> bool:
    seen = set() if seen is None else seen
    if id(error) in seen:
        return False
    seen.add(id(error))
    if isinstance(error, ScanDeadlineError):
        return True
    if isinstance(error, BaseExceptionGroup):
        return any(_contains_scan_deadline(item, seen) for item in error.exceptions)
    return any(
        _contains_scan_deadline(cause, seen)
        for cause in (error.__cause__, error.__context__)
        if cause is not None
    )


@dataclass(slots=True)
class _ScanWork:
    sources: _SourceAcquisition
    persistence: _DetectorPersistence
    runtime_identity: str
    context_events: tuple[DerivedEvent, ...] = ()
    terminal_status: str = "running"
    error_class: str | None = None
    failure: BaseException | None = None
    deadline_exceeded: bool = False
    window: _ScanWindow | None = None
    drain: FinalDrainResult | None = None
    source_lag: dict[tuple[str, str, str], int] | None = None
    observations: list[DerivedEvent] = field(default_factory=list)
    context_observed: bool = False
    source_processed: bool = False

    def fail(self, error: BaseException, phase: str) -> None:
        self.deadline_exceeded |= _contains_scan_deadline(error)
        if self.failure is None:
            self.failure = error
            self.error_class = (
                "scan_timeout"
                if self.deadline_exceeded
                else ("capability" if isinstance(error, CapabilityError) else phase)
            )
        else:
            self.failure = BaseExceptionGroup(
                "Multiple scan boundaries failed", [self.failure, error]
            )
            self.error_class = "multiple"
        self.terminal_status = "failed"


def _execute_scan_work(request: _LeasedScanRequest, work: _ScanWork) -> None:
    try:
        quick_check(request.connection)
        work.window = _prepare_scan_window(
            request.connection,
            config=request.config,
            source=request.source,
            end_ns=_datetime_ns(request.now),
        )
        with request.connection:
            request.connection.execute(
                "UPDATE scan_runs SET source_start_ns = ?, source_end_ns = ? WHERE id = ?",
                (work.window.start_ns, work.window.end_ns, request.scan_run_id),
            )
    except BaseException as exc:
        if request.connection.in_transaction:
            request.connection.rollback()
        work.fail(exc, "processing")
        return
    try:
        work.context_events, work.persistence = _process_context_phase(request, work.persistence)
        work.context_observed = True
    except BaseException as exc:
        if request.connection.in_transaction:
            request.connection.rollback()
        work.fail(exc, "processing")
        return
    try:
        work.sources, work.persistence, work.terminal_status = _process_source_phase(
            request, work.window, work.sources, work.persistence
        )
        work.source_processed = True
    except BaseException as exc:
        if request.connection.in_transaction:
            request.connection.rollback()
        work.fail(exc, "processing")


def _capture_scan_observations(request: _LeasedScanRequest, work: _ScanWork) -> None:
    observed_at_ns = _datetime_ns(datetime.now(UTC))
    audit = capture_remote_integrity(
        request.source,
        scan_run_id=request.scan_run_id,
        observed_at_ns=observed_at_ns,
        runtime_identity=work.runtime_identity,
    )
    work.observations.extend(audit)
    if any(event.event_name == INTEGRITY_INCIDENT_EVENT for event in audit):
        work.fail(ScanError("canonical telemetry integrity validation failed"), "integrity")
    work.observations.extend(
        capture_lifecycle_authority(
            request.connection,
            observed_at_ns=observed_at_ns,
            scan_run_id=request.scan_run_id,
            runtime_identity=work.runtime_identity,
        )
    )
    if work.window is not None and work.terminal_status != "failed":
        work.source_lag = capture_source_lag(
            connection=request.connection,
            client=request.source,
            extraction_bound_ns=work.window.end_ns,
        )


def _observe_scan_work(request: _LeasedScanRequest, work: _ScanWork) -> None:
    if work.deadline_exceeded:
        return
    try:
        _capture_scan_observations(request, work)
    except BaseException as exc:
        work.fail(exc, "observation")
    if work.deadline_exceeded:
        return
    try:
        work.drain = capture_final_drain(
            request.connection,
            scan_run_id=request.scan_run_id,
            endpoint=f"{request.config.signoz.otlp_http_endpoint.rstrip('/')}/v1/logs",
        )
    except BaseException as exc:
        work.fail(exc, "telemetry")
    if work.deadline_exceeded:
        return
    try:
        maintenance = capture_maintenance(
            request.connection,
            scan_run_id=request.scan_run_id,
            database_path=request.config.database.path,
            runtime_identity=work.runtime_identity,
        )
        work.observations.append(maintenance)
        if maintenance.attributes["database.check_result"] != "ok":
            raise ScanError("final ledger integrity check failed")
    except BaseException as exc:
        work.fail(exc, "ledger")


def _completed_scan_snapshot(
    request: _LeasedScanRequest,
    work: _ScanWork,
    completed_at_ns: int,
    *,
    monotonic_finished: float,
) -> DerivedEvent:
    snapshot = _pipeline_snapshot_event(
        _PipelineSnapshotRequest(
            scan_run_id=request.scan_run_id,
            end_ns=work.window.end_ns if work.window is not None else _datetime_ns(request.now),
            terminal_status=work.terminal_status,
            error_class=work.error_class,
            logs=work.sources.logs_stream,
            traces=work.sources.traces_stream,
            hydration=work.sources.hydration_stream,
            finished_ns=completed_at_ns,
            duration_ms=(monotonic_finished - request.started) * 1000,
            rows_processed=(
                len(work.sources.logs) + len(work.sources.traces)
                if (
                    work.sources.logs_stream.query_status == "available"
                    and work.sources.traces_stream.query_status == "available"
                )
                else None
            ),
            logs_count=(
                len(work.sources.logs)
                if work.sources.logs_stream.query_status == "available"
                else None
            ),
            traces_count=(
                len(work.sources.traces)
                if work.sources.traces_stream.query_status == "available"
                else None
            ),
            context_events_count=(len(work.context_events) if work.context_observed else None),
            canonical_activities_count=(
                len(work.persistence.activities) if work.source_processed else None
            ),
            pending_after_drain=work.drain.pending_events if work.drain is not None else None,
            failed_during_drain=work.drain.failed_events if work.drain is not None else None,
            runtime_identity=work.runtime_identity,
            schedule_interval_seconds=request.config.scheduler.interval_seconds,
            schedule_timezone=request.config.scheduler.timezone,
        )
    )
    attributes = dict(snapshot.attributes)
    fingerprint = implementation_fingerprint()
    attributes["scan.deployment_fingerprint"] = fingerprint
    attributes["scan.projection_fingerprint"] = fingerprint
    for prefix, name in (
        ("lifecycle", "introspection.session_context.population"),
        ("integrity", "introspection.pipeline.integrity_audit"),
    ):
        observation = next((event for event in work.observations if event.event_name == name), None)
        attributes[f"{prefix}.capture_state"] = (
            "completed" if observation is not None else "unavailable"
        )
        if observation is not None:
            attributes[f"{prefix}.observation_event_id"] = observation.event_id
            attributes[f"{prefix}.observed_at_ns"] = str(observation.timestamp_ns)
    return replace(snapshot, attributes=attributes)


def _record_scan_execution_inputs(
    connection: sqlite3.Connection,
    request: _LeasedScanRequest,
    work: _ScanWork,
    *,
    monotonic_finished: float,
) -> None:
    sources = work.sources
    connection.execute(
        """
        INSERT INTO scan_execution_inputs (
            scan_run_id, monotonic_started, monotonic_finished, logs_json, traces_json,
            context_events_json, canonical_activities_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            request.scan_run_id,
            request.started,
            monotonic_finished,
            (
                json.dumps([log.log_id for log in sources.logs], separators=(",", ":"))
                if sources.logs_stream.query_status == "available"
                else None
            ),
            (
                json.dumps([trace.trace_id for trace in sources.traces], separators=(",", ":"))
                if sources.traces_stream.query_status == "available"
                else None
            ),
            (
                json.dumps(
                    [event.entity_id for event in work.context_events], separators=(",", ":")
                )
                if work.context_observed
                else None
            ),
            (
                json.dumps(work.persistence.canonical_activity_ids, separators=(",", ":"))
                if work.source_processed
                else None
            ),
        ),
    )


def _record_snapshot_oracle(connection: sqlite3.Connection, snapshot: DerivedEvent) -> None:
    connection.execute(
        """
        INSERT INTO pipeline_snapshot_oracle (
            event_id, scan_run_id, payload_schema_version, completed_at_ns,
            extraction_bound_ns, terminal_status, error_class, duration_ms,
            rows_processed, logs_count, traces_count, context_events_count,
            canonical_activities_count, pending_after_drain, failed_during_drain,
            runtime_identity, schedule_interval_seconds, schedule_timezone
        ) VALUES (?, ?, 2, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot.event_id,
            snapshot.entity_id,
            snapshot.attributes["scan.completed_at_ns"],
            snapshot.attributes["scan.extraction_bound_ns"],
            snapshot.attributes["scan.terminal_status"],
            snapshot.attributes.get("pipeline.error_class"),
            snapshot.attributes["scan.duration_ms"],
            snapshot.attributes.get("rows.processed"),
            snapshot.attributes.get("logs.count"),
            snapshot.attributes.get("traces.count"),
            snapshot.attributes.get("context.events_count"),
            snapshot.attributes.get("canonical.activities_count"),
            snapshot.attributes.get("outbox.pending_after_drain"),
            snapshot.attributes.get("outbox.failed_during_drain"),
            snapshot.attributes["scan.runtime_identity"],
            snapshot.attributes["scan.schedule_interval_seconds"],
            snapshot.attributes["scan.schedule_timezone"],
        ),
    )


def _finish_scan(request: _LeasedScanRequest, work: _ScanWork) -> None:
    completed_at = datetime.now(UTC)
    completed_at_ns = _datetime_ns(completed_at)
    monotonic_finished = time.monotonic()
    snapshot = _completed_scan_snapshot(
        request, work, completed_at_ns, monotonic_finished=monotonic_finished
    )
    if work.source_lag is not None and work.window is not None:
        work.observations.extend(
            source_lag_events(
                work.source_lag,
                scan_run_id=request.scan_run_id,
                extraction_bound_ns=work.window.end_ns,
                completed_at_ns=completed_at_ns,
            )
        )
    with request.connection:
        request.connection.execute(
            """
            UPDATE scan_runs
            SET status = ?, completed_at = ?, rows_processed = ?, error_code = ?, details_json = ?
            WHERE id = ?
            """,
            (
                work.terminal_status,
                completed_at.isoformat(),
                len(work.sources.logs) + len(work.sources.traces),
                type(work.failure).__name__ if work.failure is not None else work.error_class,
                _scan_details(work.sources, work.persistence, work.context_events),
                request.scan_run_id,
            ),
        )
        if work.drain is not None:
            enqueue_final_drain_projection(request.connection, work.drain)
        enqueue_events(request.connection, [snapshot, *work.observations])
        record_observation_events(
            request.connection, work.observations, observed_at_ns=completed_at_ns
        )
        _record_scan_execution_inputs(
            request.connection, request, work, monotonic_finished=monotonic_finished
        )
        _record_snapshot_oracle(request.connection, snapshot)


def _run_leased_scan(request: _LeasedScanRequest) -> dict[str, Any]:
    work = _ScanWork(
        sources=_SourceAcquisition(
            [], [], [], [], PipelineStream(), PipelineStream(), PipelineStream()
        ),
        persistence=_DetectorPersistence(
            [],
            [],
            [],
            {"included": 0, "attributed": 0, "expected_rejection": 0, "failed": 0, "blocked": 0},
        ),
        runtime_identity=_pipeline_runtime_identity(request.config),
    )
    try:
        _begin_scan_run(request, _datetime_ns(request.now))
        _execute_scan_work(request, work)
        _observe_scan_work(request, work)
    finally:
        _disarm_scan_deadline(request.deadline)
    _finish_scan(request, work)
    if work.failure is not None:
        raise work.failure
    return {
        "scan_run_id": request.scan_run_id,
        "status": work.terminal_status,
        "logs": len(work.sources.logs),
        "traces": len(work.sources.traces),
        "observations": len(work.persistence.activities),
        "trend_evaluations": len(work.persistence.trend_evaluations),
        "session_context_events": len(work.context_events),
        "recovered_interrupted_scan_runs": 0,
        "telemetry_delivered": work.drain.delivered_events if work.drain is not None else None,
        "conservation": work.persistence.source_conservation,
        "telemetry_pending": int(
            request.connection.execute(
                "SELECT COUNT(*) FROM otlp_outbox WHERE status = 'pending'"
            ).fetchone()[0]
        ),
    }

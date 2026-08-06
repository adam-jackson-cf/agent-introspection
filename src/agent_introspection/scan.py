"""Transactional extraction, detection, trend evaluation, and derived events."""

from __future__ import annotations

import hashlib
import json
import signal
import sqlite3
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agent_introspection import scheduler
from agent_introspection.attribution import (
    canonical_activity_event_attributes,
    reconcile_activity,
    resolve_attribution,
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
from agent_introspection.scheduler import recover_interrupted_scan_runs
from agent_introspection.session_context import drain_inbox, inbox_path
from agent_introspection.source import ClickHouseClient, HydrationRow, LogRow, TraceRow
from agent_introspection.telemetry import (
    OPERATIONAL_SCOPE,
    CanonicalActivityVersionEvent,
    DerivedEvent,
    drain_outbox,
    enqueue_canonical_activity_version,
    enqueue_events,
)
from agent_introspection.trends import (
    TrendEvaluation,
    recompute_canonical_findings,
)


class ScanError(RuntimeError):
    """A scan cannot safely commit its extraction window."""


class ScanDeadlineExceeded(ScanError):
    """A scan exceeded its bounded execution window."""


_SCAN_TIMEOUT_SECONDS = 900.0


def _arm_scan_deadline() -> tuple[Any, tuple[float, float]]:
    """Arm the process-wide deadline that bounds all scan work."""

    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    if previous_timer != (0.0, 0.0):
        raise ScanError("scan deadline timer is already active")
    previous_handler = signal.getsignal(signal.SIGALRM)

    def expire(_signum: int, _frame: object) -> None:
        raise ScanDeadlineExceeded(f"scan exceeded {_SCAN_TIMEOUT_SECONDS:.0f} second deadline")

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


def _pipeline_snapshot_event(
    *,
    scan_run_id: str,
    end_ns: int,
    terminal_status: str,
    error_class: str | None,
    logs: PipelineStream,
    traces: PipelineStream,
    hydration: PipelineStream,
    finished_ns: int,
    duration_ms: float,
    rows_processed: int,
    pending_after_drain: int,
) -> DerivedEvent:
    logs_lag_state, logs_lag_ms = _stream_lag(logs, finished_ns=finished_ns)
    traces_lag_state, traces_lag_ms = _stream_lag(traces, finished_ns=finished_ns)
    freshness = _freshness(
        terminal_status=terminal_status,
        logs=logs,
        traces=traces,
        finished_ns=finished_ns,
    )
    attributes: dict[str, str | int | float | bool] = {
        "pipeline.state": _pipeline_state(
            terminal_status=terminal_status,
            freshness=freshness,
            logs=logs,
            traces=traces,
            hydration=hydration,
        ),
        "scan.terminal_status": terminal_status,
        "pipeline.freshness": freshness,
        "logs.query_status": logs.query_status,
        "logs.data_state": logs.data_state,
        "traces.query_status": traces.query_status,
        "traces.data_state": traces.data_state,
        "hydration.query_status": hydration.query_status,
        "hydration.data_state": hydration.data_state,
        "logs.lag_state": logs_lag_state,
        "traces.lag_state": traces_lag_state,
        "scan.duration_ms": duration_ms,
        "rows.processed": rows_processed,
        "outbox.pending_after_drain_excluding_terminal_event": pending_after_drain,
    }
    if error_class is not None:
        attributes["pipeline.error_class"] = error_class
    if logs.latest_timestamp_ns is not None:
        attributes["logs.latest_timestamp_ns"] = logs.latest_timestamp_ns
    if traces.latest_timestamp_ns is not None:
        attributes["traces.latest_timestamp_ns"] = traces.latest_timestamp_ns
    if logs_lag_ms is not None:
        attributes["logs.lag_ms"] = logs_lag_ms
    if traces_lag_ms is not None:
        attributes["traces.lag_ms"] = traces_lag_ms
    return DerivedEvent(
        scope=OPERATIONAL_SCOPE,
        entity_id=scan_run_id,
        entity_version=1,
        event_sequence=1,
        event_name="introspection.pipeline.snapshot",
        attributes=attributes,
        timestamp_ns=end_ns,
    )


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _initial_start_ns(end_ns: int) -> int:
    return max(0, end_ns - int(timedelta(days=7).total_seconds() * 1_000_000_000))


def _bounds(connection: sqlite3.Connection, end_ns: int) -> tuple[int, int, int, int]:
    row = connection.execute(
        "SELECT timestamp_ns FROM source_watermarks WHERE source = 'signoz_logs'"
    ).fetchone()
    start_ns = int(row[0]) if row is not None else _initial_start_ns(end_ns)
    if start_ns >= end_ns:
        start_ns = max(0, end_ns - 1)
    start_bucket = max(0, start_ns // 1_000_000_000 - 1800)
    end_bucket = end_ns // 1_000_000_000
    return start_ns, end_ns, start_bucket, end_bucket


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


def _persist_context_projects(
    connection: sqlite3.Connection, context_events: tuple[DerivedEvent, ...]
) -> None:
    event_ids = tuple(event.entity_id for event in context_events)
    if not event_ids:
        return
    rows = connection.execute(
        """
        SELECT project_id, project_name, project_root, project_kind
        FROM session_context_intervals
        WHERE event_id IN ({})
        """.format(",".join("?" for _ in event_ids)),
        event_ids,
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
    if attribution.evidence_id is None:
        raise ScanError("resolved canonical attribution lacks context evidence")
    row = connection.execute(
        """
        SELECT project_id, project_name, project_root, project_kind
        FROM session_context_intervals
        WHERE event_id = ? AND project_id = ?
        """,
        (attribution.evidence_id, attribution.project_identity_id),
    ).fetchone()
    if row is None:
        raise ScanError("resolved canonical attribution lacks a context project identity")
    _persist_projects(
        connection,
        {str(row[0]): ProjectIdentity(str(row[3]), Path(str(row[2])), str(row[0]), str(row[1]))},
    )


def _persist_canonical_activities(
    connection: sqlite3.Connection,
    activities: list[CanonicalActivity],
    *,
    now: datetime,
) -> tuple[list[str], list[TrendEvaluation]]:
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
    return changed_ids, evaluations


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


def run_scan(
    connection: sqlite3.Connection,
    config: AppConfig,
    *,
    client: ClickHouseClient | None = None,
    end_time: datetime | None = None,
) -> dict[str, Any]:
    """Run one fail-closed canonical extraction and reconciliation window."""
    started = time.monotonic()
    now = end_time or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("scan end_time must be timezone-aware")
    end_ns = int(now.astimezone(UTC).timestamp() * 1_000_000_000)
    scan_run_id = str(uuid.uuid4())
    source = client or ClickHouseClient(
        docker_context=config.signoz.docker_context,
        container=config.signoz.clickhouse_container,
    )
    quick_check(connection)
    logs_stream = PipelineStream()
    traces_stream = PipelineStream()
    hydration_stream = PipelineStream()
    logs: list[LogRow] = []
    traces: list[TraceRow] = []
    hydration: list[HydrationRow] = []
    activities: list[CanonicalActivity] = []
    trend_evaluations: list[TrendEvaluation] = []
    context_events: tuple[DerivedEvent, ...] = ()
    terminal_status = "failed"
    error_class: str | None = None
    failure: BaseException | None = None
    scan_run_persisted = False
    recovered_interrupted_scan_runs: tuple[str, ...] = ()
    telemetry_delivered = 0
    pending_after_drain = 0
    deadline = _arm_scan_deadline()
    try:
        lease = scheduler.acquire_lease(
            connection, duration=timedelta(seconds=config.scheduler.lease_seconds)
        )
    except BaseException:
        _disarm_scan_deadline(deadline)
        raise
    deadline_armed = True
    try:
        recovered_interrupted_scan_runs = recover_interrupted_scan_runs(connection)
        start_ns, end_ns, start_bucket, end_bucket = _bounds(connection, end_ns)
        started_at = _iso_now()
        try:
            verify_network_perimeter(docker_context=config.signoz.docker_context)
            enforce_approved_schema(connection, discover_source_schema(source))
            with connection:
                connection.execute(
                    """
                    INSERT INTO scan_runs (
                        id, status, started_at, source_start_ns, source_end_ns, details_json
                    ) VALUES (?, 'running', ?, ?, ?, '{}')
                    """,
                    (scan_run_id, started_at, start_ns, end_ns),
                )
            scan_run_persisted = True
            context_events = drain_inbox(connection, directory=inbox_path(config.database.path))
            with connection:
                enqueue_events(connection, list(context_events))
                _persist_context_projects(connection, context_events)
            late_activity_ids = _reconcile_late_context(connection, context_events)
            if late_activity_ids:
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    trend_evaluations.extend(
                        recompute_canonical_findings(connection, late_activity_ids, now=now)
                    )
                    connection.execute(
                        """
                        UPDATE canonical_recomputation_schedule
                        SET completed_at = ?
                        WHERE activity_id IN ({}) AND completed_at IS NULL
                        """.format(",".join("?" for _ in late_activity_ids)),
                        (_iso_now(), *late_activity_ids),
                    )
            logs = list(
                source.logs(
                    start_ns=start_ns,
                    end_ns=end_ns,
                    start_bucket=start_bucket,
                    end_bucket=end_bucket,
                )
            )
            logs_stream = PipelineStream(
                query_status="available",
                data_state="records" if logs else "no_data",
                latest_timestamp_ns=max((log.timestamp_ns for log in logs), default=None),
            )
            start_dt = datetime.fromtimestamp(start_ns / 1_000_000_000, tz=UTC)
            end_dt = datetime.fromtimestamp(end_ns / 1_000_000_000, tz=UTC)
            traces = list(
                source.traces(
                    start=start_dt, end=end_dt, start_bucket=start_bucket, end_bucket=end_bucket
                )
            )
            traces_stream = PipelineStream(
                query_status="available",
                data_state="records" if traces else "no_data",
                latest_timestamp_ns=max(
                    (int(trace.ended_at.timestamp() * 1_000_000_000) for trace in traces),
                    default=None,
                ),
            )
            trace_index = {trace.trace_id: trace for trace in traces}
            shortlisted = _shortlisted_log_ids(logs, trace_index)
            for offset in range(0, len(shortlisted), 250):
                hydration.extend(
                    source.hydrate(
                        identity_kind="log_id",
                        identifiers=shortlisted[offset : offset + 250],
                        start_ns=start_ns,
                        end_ns=end_ns,
                        start_bucket=start_bucket,
                        end_bucket=end_bucket,
                    )
                )
            hydration_stream = PipelineStream(
                query_status="available", data_state="records" if hydration else "no_data"
            )
            events = _detector_events(logs, traces, hydration)
            token_baselines: dict[str, list[int]] = defaultdict(list)
            for event in events:
                if event.token_count is not None:
                    token_baselines[event.project_id].append(event.token_count)
            observations = DetectorEngine().detect(events, token_baselines=token_baselines)
            event_index = {event.event_id: event for event in events}
            logs_by_id = {log.log_id: log for log in logs}
            traces_by_id = {trace.trace_id: trace for trace in traces}
            observations = _partition_observations_by_context(
                connection, observations, event_index, traces_by_id, logs_by_id
            )
            activities = [
                _canonical_activity(observation, event_index, logs_by_id, traces_by_id)
                for observation in observations
            ]
            connection.execute("BEGIN IMMEDIATE")
            _persist_source_rejections(connection, traces)
            _, current_evaluations = _persist_canonical_activities(connection, activities, now=now)
            trend_evaluations.extend(current_evaluations)
            _ensure_current_activity_outbox(connection)
            terminal_status = "no_data" if not logs and not traces else "succeeded"
            connection.commit()
        except BaseException as exc:
            failure = exc
            if connection.in_transaction:
                connection.rollback()
            terminal_status = "failed"
            if isinstance(exc, ScanDeadlineExceeded):
                error_class = "scan_timeout"
            elif isinstance(exc, CapabilityError):
                error_class = "capability"
            else:
                error_class = "processing"
        try:
            for _ in range(20):
                drain = drain_outbox(
                    connection,
                    endpoint=f"{config.signoz.otlp_http_endpoint.rstrip('/')}/v1/logs",
                    limit=500,
                )
                telemetry_delivered += drain["delivered"]
                if drain["selected"] == 0 or drain["delivered"] == 0:
                    break
            pending_after_drain = int(
                connection.execute(
                    "SELECT COUNT(*) FROM otlp_outbox WHERE status = 'pending'"
                ).fetchone()[0]
            )
        except BaseException as exc:
            failure = exc
            terminal_status = "failed"
            error_class = "scan_timeout" if isinstance(exc, ScanDeadlineExceeded) else "telemetry"
        finally:
            _disarm_scan_deadline(deadline)
            deadline_armed = False
        finished_ns = time.time_ns()
        snapshot = _pipeline_snapshot_event(
            scan_run_id=scan_run_id,
            end_ns=end_ns,
            terminal_status=terminal_status,
            error_class=error_class,
            logs=logs_stream,
            traces=traces_stream,
            hydration=hydration_stream,
            finished_ns=finished_ns,
            duration_ms=(time.monotonic() - started) * 1000,
            rows_processed=len(logs) + len(traces),
            pending_after_drain=pending_after_drain,
        )
        details_json = json.dumps(
            {
                "hydrated": len(hydration),
                "logs": len(logs),
                "canonical_activities": len(activities),
                "traces": len(traces),
                "trends": len(trend_evaluations),
                "session_context_events": len(context_events),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        with connection:
            if scan_run_persisted:
                connection.execute(
                    """
                    UPDATE scan_runs
                    SET status = ?, completed_at = ?, rows_processed = ?, error_code = ?,
                        details_json = ?
                    WHERE id = ?
                    """,
                    (
                        terminal_status,
                        datetime.now(UTC).isoformat(),
                        len(logs) + len(traces),
                        type(failure).__name__ if failure is not None else error_class,
                        details_json,
                        scan_run_id,
                    ),
                )
            enqueue_events(connection, [snapshot])
        pending = int(
            connection.execute(
                "SELECT COUNT(*) FROM otlp_outbox WHERE status = 'pending'"
            ).fetchone()[0]
        )
        if failure is not None:
            raise failure
        return {
            "scan_run_id": scan_run_id,
            "status": terminal_status,
            "logs": len(logs),
            "traces": len(traces),
            "observations": len(activities),
            "trend_evaluations": len(trend_evaluations),
            "session_context_events": len(context_events),
            "recovered_interrupted_scan_runs": len(recovered_interrupted_scan_runs),
            "telemetry_delivered": telemetry_delivered,
            "telemetry_pending": pending,
        }
    finally:
        if deadline_armed:
            _disarm_scan_deadline(deadline)
        scheduler.release_lease(connection, lease)

"""Transactional extraction, detection, trend evaluation, and derived events."""

from __future__ import annotations

import hashlib
import json
import signal
import sqlite3
import time
import uuid
from collections import defaultdict
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agent_introspection import scheduler
from agent_introspection.capabilities import (
    CapabilityError,
    discover_source_schema,
    enforce_approved_schema,
    verify_network_perimeter,
)
from agent_introspection.config import AppConfig
from agent_introspection.database import (
    ObservationRecord,
    SourceWatermark,
    persist_observations_and_watermark,
    quick_check,
)
from agent_introspection.detectors import DetectorEngine, DetectorEvent, Observation
from agent_introspection.evidence import HydratedEvidence, hydrate_allowlisted_fields
from agent_introspection.generations import GenerationError, validate_active_generation_contract
from agent_introspection.identities import (
    ProjectIdentity,
    canonical_task,
    discover_project,
)
from agent_introspection.normalization import NormalizationError, normalize_tool_operation
from agent_introspection.outcomes import derive_outcome
from agent_introspection.review import record_review_activity_snapshot
from agent_introspection.scheduler import recover_interrupted_scan_runs
from agent_introspection.source import ClickHouseClient, HydrationRow, LogRow, TraceRow
from agent_introspection.telemetry import (
    OPERATIONAL_SCOPE,
    DerivedEvent,
    drain_outbox,
    enqueue_events,
)
from agent_introspection.trends import (
    Occurrence,
    TrendEvaluation,
    TrendState,
    evaluate_findings,
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
    active_generation: str | None,
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
    if active_generation is not None:
        attributes["analysis.generation"] = active_generation
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


def _stable_id(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


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


def _trace_indexes(
    logs: list[LogRow], traces: list[TraceRow]
) -> tuple[dict[str, TraceRow], dict[str, str]]:
    by_trace = {trace.trace_id: trace for trace in traces}
    candidates: dict[str, set[str]] = defaultdict(set)
    for log in logs:
        trace = by_trace.get(log.trace_id or "")
        if log.conversation_id and trace is not None and trace.thread_id:
            candidates[log.conversation_id].add(trace.thread_id)
    conversation_map = {
        conversation: next(iter(thread_ids))
        for conversation, thread_ids in candidates.items()
        if len(thread_ids) == 1
    }
    return by_trace, conversation_map


def _project_for_trace(trace: TraceRow | None) -> ProjectIdentity | None:
    if trace is None or trace.cwd is None:
        return None
    try:
        return discover_project(trace.cwd)
    except (OSError, ValueError):
        return None


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
                else log.conversation_id or log.trace_id or log.log_id
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
) -> tuple[list[DetectorEvent], dict[str, ProjectIdentity]]:
    by_trace, conversation_map = _trace_indexes(logs, traces)
    hydration_by_id = {row.log_id: row for row in hydration}
    operations = _hydrated_operations(hydration)
    projects: dict[str, ProjectIdentity] = {}
    events: list[DetectorEvent] = []
    mutation_tools = {"apply_patch", "write_file", "edit_file", "create_file"}
    for log in logs:
        trace = by_trace.get(log.trace_id or "")
        task = canonical_task(
            trace_id=log.trace_id or log.log_id,
            thread_id=trace.thread_id if trace else None,
            conversation_id=log.conversation_id,
            conversation_to_thread=conversation_map,
        )
        project = _project_for_trace(trace)
        project_id = project.identity if project else f"unresolved:{task.canonical}"
        if project is not None:
            projects[project.identity] = project
        hydrated = hydration_by_id.get(log.log_id)
        operation = operations.get(log.log_id)
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
                project_id=project_id,
                task_id=task.canonical,
                event_name=event_name,
                operation=operation,
                success_string=log.success_string,
                success_bool=log.success_bool,
                status_code=log.status_code,
                outcome=outcome,
                is_mutation=bool(log.tool_name in mutation_tools),
                counts_as_distinct_task=task.counts_as_distinct_task,
            )
        )
    for trace in traces:
        if trace.total_tokens <= 0:
            continue
        task = canonical_task(
            trace_id=trace.trace_id,
            thread_id=trace.thread_id,
            conversation_id=None,
            conversation_to_thread=conversation_map,
        )
        project = _project_for_trace(trace)
        project_id = project.identity if project else f"unresolved:{task.canonical}"
        if project is not None:
            projects[project.identity] = project
        events.append(
            DetectorEvent(
                event_id=f"trace:{trace.trace_id}",
                timestamp=trace.ended_at,
                project_id=project_id,
                task_id=task.canonical,
                event_name="trace.episode",
                token_count=trace.total_tokens,
                counts_as_distinct_task=task.counts_as_distinct_task,
            )
        )
    return events, projects


def _persist_projects(connection: sqlite3.Connection, projects: dict[str, ProjectIdentity]) -> None:
    now = _iso_now()
    for project in projects.values():
        identity_kind = "git" if project.kind == "git" else "non_git"
        connection.execute(
            """
            INSERT INTO project_identities (
                id, identity_kind, canonical_path, git_common_dir, created_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (
                project.identity,
                identity_kind,
                project.root.as_posix(),
                (project.root / ".git").as_posix() if identity_kind == "git" else None,
                now,
            ),
        )


def _records(
    scan_run_id: str,
    observations: tuple[Observation, ...],
    event_index: dict[str, DetectorEvent],
    projects: dict[str, ProjectIdentity],
) -> list[ObservationRecord]:
    now = _iso_now()
    records: list[ObservationRecord] = []
    for observation in observations:
        first = event_index[observation.event_ids[0]]
        components = observation.fingerprint_components
        records.append(
            ObservationRecord(
                id=_stable_id(observation.fingerprint, *observation.event_ids),
                scan_run_id=scan_run_id,
                detector_id=observation.detector_id,
                detector_version=observation.detector_version,
                category=observation.category,
                project_identity_id=(
                    observation.project_id if observation.project_id in projects else None
                ),
                task_identity=first.task_id,
                turn_identity=None,
                occurred_at_ns=int(first.timestamp.timestamp() * 1_000_000_000),
                fingerprint=observation.fingerprint,
                operation_kind=components.operation_kind,
                target_kind=components.target_kind,
                normalized_target=components.normalized_target,
                normalized_failure_class=components.normalized_failure_class,
                normalization_version=1,
                membership_explanation=observation.membership_explanation,
                attributes={"event_ids": list(observation.event_ids)},
                created_at=now,
            )
        )
    return records


def _persist_evidence(
    connection: sqlite3.Connection,
    records: list[ObservationRecord],
    hydration: list[HydrationRow],
) -> None:
    by_id = {row.log_id: row for row in hydration}
    allowed = frozenset(
        {
            "arguments",
            "args",
            "argv",
            "assistant_output",
            "error_message",
            "outcome",
            "diagnostic_code",
        }
    )
    now = _iso_now()
    for record in records:
        event_ids = record.attributes.get("event_ids", [])
        for event_id in event_ids:
            row = by_id.get(str(event_id))
            if row is None:
                evidence_kind = "source_reference"
                hydrated = HydratedEvidence(
                    correlation_status="pending",
                    redacted_content=None,
                    content_hash=hashlib.sha256(b"pending").hexdigest(),
                    source_reference=f"signoz-log:{event_id}",
                )
            else:
                fields = {
                    "arguments": row.arguments,
                    "args": row.args,
                    "argv": row.argv,
                    "assistant_output": row.assistant_output,
                    "error_message": row.error_message,
                    "outcome": row.outcome,
                    "diagnostic_code": row.diagnostic_code,
                }
                evidence_kind = "hydrated_log"
                hydrated = hydrate_allowlisted_fields(
                    source_reference=f"signoz-log:{event_id}",
                    fields=fields,
                    allowed_fields=allowed,
                )
            connection.execute(
                """
                INSERT INTO evidence (
                    id, observation_id, evidence_kind, source_reference,
                    redacted_content, content_hash, correlation_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (
                    _stable_id(record.id, str(event_id), evidence_kind),
                    record.id,
                    evidence_kind,
                    hydrated.source_reference,
                    hydrated.redacted_content,
                    hydrated.content_hash,
                    hydrated.correlation_status,
                    now,
                ),
            )


def _update_findings(
    connection: sqlite3.Connection,
    records: list[ObservationRecord],
    *,
    now: datetime,
    manage_transaction: bool = True,
) -> list[TrendEventRecord]:
    if now.tzinfo is None:
        raise ValueError("trend evaluation clock must be timezone-aware")
    now = now.astimezone(UTC)
    if not manage_transaction and not connection.in_transaction:
        raise ScanError("shared trend evaluation requires an active transaction")
    cutoff_ns = int((now - timedelta(days=7)).timestamp() * 1_000_000_000)
    fingerprints = sorted({record.fingerprint for record in records})
    for fingerprint in fingerprints:
        finding_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"agent-introspection:{fingerprint}"))
        related = [record for record in records if record.fingerprint == fingerprint]
        first = related[0]
        with connection if manage_transaction else nullcontext():
            connection.execute(
                """
                INSERT INTO findings (
                    id, fingerprint, category, project_identity_id, trend_state,
                    detector_id, detector_version, first_seen_ns, last_seen_ns,
                    occurrence_count, canonical_task_count, local_day_count,
                    entity_version, updated_at
                ) VALUES (?, ?, ?, ?, 'isolated', ?, ?, ?, ?, 1, 0, 1, 1, ?)
                ON CONFLICT(fingerprint) DO UPDATE SET
                    last_seen_ns = max(last_seen_ns, excluded.last_seen_ns),
                    occurrence_count = occurrence_count + 1,
                    entity_version = entity_version + 1,
                    updated_at = excluded.updated_at
                """,
                (
                    finding_id,
                    fingerprint,
                    first.category,
                    first.project_identity_id,
                    first.detector_id,
                    first.detector_version,
                    first.occurred_at_ns,
                    first.occurred_at_ns,
                    now.isoformat(),
                ),
            )
            actual_finding = connection.execute(
                "SELECT id FROM findings WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()[0]
            for record in related:
                connection.execute(
                    """
                    INSERT INTO finding_membership (
                        finding_id, observation_id, rationale, created_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(finding_id, observation_id) DO NOTHING
                    """,
                    (
                        actual_finding,
                        record.id,
                        record.membership_explanation,
                        now.isoformat(),
                    ),
                )

    rows = connection.execute(
        """
        SELECT f.id, f.trend_state, o.id, o.occurred_at_ns, o.task_identity
        FROM findings f
        LEFT JOIN finding_membership fm ON fm.finding_id = f.id
        LEFT JOIN observations o ON o.id = fm.observation_id AND o.occurred_at_ns >= ?
        """,
        (cutoff_ns,),
    ).fetchall()
    occurrences = [
        Occurrence(str(row[2]), str(row[0]), int(row[3]), row[4])
        for row in rows
        if row[2] is not None
    ]
    previous = {str(row[0]) for row in rows if row[1] == TrendState.ACTIONABLE}
    evaluations = evaluate_findings(occurrences, now=now, previously_actionable=previous)
    trend_events: list[TrendEventRecord] = []
    with connection if manage_transaction else nullcontext():
        for evaluation in evaluations:
            current = connection.execute(
                """
                SELECT trend_state, entity_version, category, project_identity_id, detector_id
                FROM findings WHERE id = ?
                """,
                (evaluation.finding_id,),
            ).fetchone()
            entity_version = int(current[1]) + 1
            connection.execute(
                """
                UPDATE findings SET trend_state = ?, occurrence_count = ?,
                    canonical_task_count = ?, local_day_count = ?,
                    entity_version = ?, updated_at = ? WHERE id = ?
                """,
                (
                    evaluation.state,
                    evaluation.occurrence_count,
                    evaluation.canonical_task_count,
                    evaluation.local_day_count,
                    entity_version,
                    now.isoformat(),
                    evaluation.finding_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO trend_evaluations (
                    id, finding_id, trend_state, window_start, window_end,
                    occurrence_count, canonical_task_count, local_day_count,
                    rationale, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    evaluation.finding_id,
                    evaluation.state,
                    datetime.fromtimestamp(
                        evaluation.window_started_at_ns / 1_000_000_000, tz=UTC
                    ).isoformat(),
                    datetime.fromtimestamp(
                        evaluation.window_ended_at_ns / 1_000_000_000, tz=UTC
                    ).isoformat(),
                    evaluation.occurrence_count,
                    evaluation.canonical_task_count,
                    evaluation.local_day_count,
                    "deterministic seven-day trend thresholds",
                    now.isoformat(),
                ),
            )
            trend_events.append(
                TrendEventRecord(
                    evaluation=evaluation,
                    promoted=(
                        current[0] != TrendState.ACTIONABLE
                        and evaluation.state is TrendState.ACTIONABLE
                    ),
                    entity_version=entity_version,
                    category=str(current[2]),
                    project_id=str(current[3]) if current[3] is not None else None,
                    detector_id=str(current[4]),
                )
            )
    return trend_events


def run_scan(
    connection: sqlite3.Connection,
    config: AppConfig,
    *,
    client: ClickHouseClient | None = None,
    end_time: datetime | None = None,
) -> dict[str, Any]:
    """Run one fail-closed scan and publish only a safe terminal pipeline snapshot."""
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
    records: list[ObservationRecord] = []
    trend_events: list[TrendEventRecord] = []
    active_generation: str | None = None
    terminal_status = "failed"
    error_class: str | None = None
    failure: BaseException | None = None
    terminal_only_failure = False
    scan_run_persisted = False
    recovered_interrupted_scan_runs: tuple[str, ...] = ()
    telemetry_delivered = 0
    pending_after_drain = 0
    deadline = _arm_scan_deadline()
    try:
        lease = scheduler.acquire_lease(
            connection,
            duration=timedelta(seconds=config.scheduler.lease_seconds),
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
            try:
                verify_network_perimeter(docker_context=config.signoz.docker_context)
            except CapabilityError:
                error_class = "network_perimeter"
                raise
            try:
                source_contract_fingerprint = enforce_approved_schema(
                    connection, discover_source_schema(source)
                )
            except CapabilityError:
                error_class = "source_contract"
                raise
            try:
                active_generation = validate_active_generation_contract(
                    connection,
                    source_contract_fingerprint=source_contract_fingerprint,
                )
                if active_generation is None:
                    error_class = "generation_unavailable"
                    terminal_only_failure = True
                    raise GenerationError("active analysis generation is unavailable")
            except GenerationError:
                if error_class is None:
                    error_class = "generation_contract"
                active_generation = None
                raise
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
            try:
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
            except BaseException:
                logs_stream.query_status = "failed"
                error_class = "logs_query"
                raise
            try:
                start_dt = datetime.fromtimestamp(start_ns / 1_000_000_000, tz=UTC)
                end_dt = datetime.fromtimestamp(end_ns / 1_000_000_000, tz=UTC)
                traces = list(
                    source.traces(
                        start=start_dt,
                        end=end_dt,
                        start_bucket=start_bucket,
                        end_bucket=end_bucket,
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
            except BaseException:
                traces_stream.query_status = "failed"
                error_class = "traces_query"
                raise
            try:
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
                    query_status="available",
                    data_state="records" if hydration else "no_data",
                )
            except BaseException:
                hydration_stream.query_status = "failed"
                error_class = "hydration"
                raise
            try:
                events, projects = _detector_events(logs, traces, hydration)
                token_baselines: dict[str, list[int]] = defaultdict(list)
                for event in events:
                    if event.token_count is not None:
                        token_baselines[event.project_id].append(event.token_count)
                observations = DetectorEngine().detect(events, token_baselines=token_baselines)
                event_index = {event.event_id: event for event in events}
                records = _records(scan_run_id, observations, event_index, projects)
                if records:
                    placeholders = ",".join("?" for _ in records)
                    existing_ids = {
                        str(row[0])
                        for row in connection.execute(
                            f"SELECT id FROM observations WHERE id IN ({placeholders})",
                            tuple(record.id for record in records),
                        )
                    }
                    records = [record for record in records if record.id not in existing_ids]
                connection.execute("BEGIN IMMEDIATE")
                _persist_projects(connection, projects)
                if logs:
                    last_id = logs[-1].log_id
                elif traces:
                    last_id = f"trace:{traces[-1].trace_id}"
                else:
                    last_id = "no-data"
                persist_observations_and_watermark(
                    connection,
                    records,
                    SourceWatermark("signoz_logs", end_ns, last_id, _iso_now()),
                    manage_transaction=False,
                )
                _persist_evidence(connection, records, hydration)
                trend_events = _update_findings(
                    connection,
                    records,
                    now=now,
                    manage_transaction=False,
                )
                project_names = {
                    str(row[0]): Path(str(row[1])).name
                    for row in connection.execute(
                        "SELECT id, canonical_path FROM project_identities"
                    ).fetchall()
                }
                scope = f"generation:{active_generation}"
                projection_events = [
                    DerivedEvent(
                        scope=scope,
                        entity_id=record.id,
                        entity_version=1,
                        event_sequence=1,
                        event_name="introspection.observation.detected",
                        attributes={
                            "analysis.generation": active_generation,
                            "detector.id": record.detector_id,
                            "project.id": record.project_identity_id or "unresolved",
                            "project.name": project_names.get(
                                record.project_identity_id or "", "unresolved"
                            ),
                            "finding.id": record.fingerprint,
                        },
                        timestamp_ns=record.occurred_at_ns,
                    )
                    for record in records
                ]
                projection_events.extend(
                    DerivedEvent(
                        scope=scope,
                        entity_id=trend.evaluation.finding_id,
                        entity_version=trend.entity_version,
                        event_sequence=trend.entity_version,
                        event_name=(
                            "introspection.trend.promoted"
                            if trend.promoted
                            else "introspection.trend.evaluated"
                        ),
                        attributes={
                            "analysis.generation": active_generation,
                            "trend.state": str(trend.evaluation.state),
                            "finding.category": trend.category,
                            "project.id": trend.project_id or "unresolved",
                            "project.name": project_names.get(trend.project_id or "", "unresolved"),
                            "detector.id": trend.detector_id,
                            "finding.id": trend.evaluation.finding_id,
                            "occurrence.count": trend.evaluation.occurrence_count,
                        },
                        timestamp_ns=trend.evaluation.window_ended_at_ns,
                    )
                    for trend in trend_events
                )
                enqueue_events(connection, projection_events)
                terminal_status = "no_data" if not logs and not traces else "succeeded"
                connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                if error_class is None:
                    error_class = "processing"
                raise
        except BaseException as exc:
            failure = exc
            active_generation = None
            if connection.in_transaction:
                connection.rollback()
            terminal_status = "failed"
            if isinstance(exc, ScanDeadlineExceeded):
                error_class = "scan_timeout"
            elif error_class is None:
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
            active_generation = None
            terminal_status = "failed"
            error_class = "scan_timeout" if isinstance(exc, ScanDeadlineExceeded) else "telemetry"
        finally:
            _disarm_scan_deadline(deadline)
            deadline_armed = False
        finished_ns = time.time_ns()
        duration_ms = (time.monotonic() - started) * 1000
        snapshot = _pipeline_snapshot_event(
            scan_run_id=scan_run_id,
            end_ns=end_ns,
            terminal_status=terminal_status,
            error_class=error_class,
            logs=logs_stream,
            traces=traces_stream,
            hydration=hydration_stream,
            finished_ns=finished_ns,
            duration_ms=duration_ms,
            rows_processed=len(logs) + len(traces),
            pending_after_drain=pending_after_drain,
            active_generation=active_generation,
        )
        details = {
            "hydrated": len(hydration),
            "logs": len(logs),
            "observations": len(records),
            "traces": len(traces),
            "trends": len(trend_events),
        }
        error_code = (
            error_class
            if terminal_only_failure
            else type(failure).__name__
            if failure is not None
            else error_class
        )
        details_json = json.dumps(details, sort_keys=True, separators=(",", ":"))
        terminal_at = datetime.now(UTC)
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
                        terminal_at.isoformat(),
                        len(logs) + len(traces),
                        error_code,
                        details_json,
                        scan_run_id,
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO scan_runs (
                        id, status, started_at, completed_at, source_start_ns, source_end_ns,
                        rows_processed, error_code, details_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        scan_run_id,
                        terminal_status,
                        started_at,
                        terminal_at.isoformat(),
                        start_ns,
                        end_ns,
                        len(logs) + len(traces),
                        error_code,
                        details_json,
                    ),
                )
            review_snapshot = record_review_activity_snapshot(
                connection,
                trigger_kind="scan_run",
                trigger_id=scan_run_id,
                trigger_version=1,
                timestamp=terminal_at,
            )
            enqueue_events(connection, [snapshot, review_snapshot])
        pending = int(
            connection.execute(
                "SELECT COUNT(*) FROM otlp_outbox WHERE status = 'pending'"
            ).fetchone()[0]
        )
        if failure is not None and not terminal_only_failure:
            raise failure
        return {
            "scan_run_id": scan_run_id,
            "status": terminal_status,
            "logs": len(logs),
            "traces": len(traces),
            "observations": len(records),
            "trend_evaluations": len(trend_events),
            "recovered_interrupted_scan_runs": len(recovered_interrupted_scan_runs),
            "telemetry_delivered": telemetry_delivered,
            "telemetry_pending": pending,
        }
    finally:
        if deadline_armed:
            _disarm_scan_deadline(deadline)
        scheduler.release_lease(connection, lease)

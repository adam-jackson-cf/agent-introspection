"""Fail-closed extraction from the installed SigNoz ClickHouse schema."""

from __future__ import annotations

import json
import math
import re
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from agent_introspection.project_schema import AGENT_PROJECT_SCHEMA

_PROJECT_ATTRIBUTE_KEYS = AGENT_PROJECT_SCHEMA.attribute_keys
_PROJECT_METADATA_STATES = AGENT_PROJECT_SCHEMA.metadata_states
_PROJECT_KIND_VALUES = AGENT_PROJECT_SCHEMA.kind_values
_PROJECT_ID = _PROJECT_ATTRIBUTE_KEYS["id"]
_PROJECT_NAME = _PROJECT_ATTRIBUTE_KEYS["name"]
_PROJECT_ROOT = _PROJECT_ATTRIBUTE_KEYS["root"]
_PROJECT_KIND = _PROJECT_ATTRIBUTE_KEYS["kind"]


_PARAMETER = re.compile(r"\{([a-z][a-z0-9_]*):[^}]+\}")
_DURATION_MS = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z", re.ASCII)
_QUERY_TIMEOUT_SECONDS = 600.0


LOG_QUERY = r"""
SELECT
    timestamp,
    id,
    trace_id,
    span_id,
    attributes_string['event.name'] AS event_name,
    attributes_string['conversation.id'] AS conversation_id,
    attributes_string['call_id'] AS call_id,
    attributes_string['tool_name'] AS tool_name,
    attributes_string['success'] AS success_string,
    if(mapContains(attributes_bool, 'success'), attributes_bool['success'], NULL) AS success_bool,
    attributes_string['duration_ms'] AS duration_ms,
    if(mapContains(attributes_number, 'http.response.status_code'),
       toInt64(attributes_number['http.response.status_code']), NULL) AS status_code,
    attributes_string['decision'] AS decision,
    attributes_string['source'] AS decision_source,
    if(mapContains(attributes_number, 'input_token_count'),
       toInt64(attributes_number['input_token_count']), NULL) AS input_tokens,
    if(mapContains(attributes_number, 'output_token_count'),
       toInt64(attributes_number['output_token_count']), NULL) AS output_tokens,
    if(mapContains(attributes_number, 'reasoning_token_count'),
       toInt64(attributes_number['reasoning_token_count']), NULL) AS reasoning_tokens,
    if(mapContains(attributes_number, 'prompt_length'),
       toInt64(attributes_number['prompt_length']), NULL) AS prompt_length
FROM signoz_logs.distributed_logs_v2
WHERE timestamp > {start_ns:UInt64}
  AND timestamp <= {end_ns:UInt64}
  AND ts_bucket_start BETWEEN {start_bucket:UInt64} AND {end_bucket:UInt64}
  AND resource.`service.name`::String IN ('codex_cli_rs', 'codex-app-server')
ORDER BY timestamp, id
""".strip()


TRACE_QUERY = f"""
WITH
    attributes_string['{_PROJECT_ID}'] != '' AS project_id_present,
    attributes_string['{_PROJECT_NAME}'] != '' AS project_name_present,
    attributes_string['{_PROJECT_ROOT}'] != '' AS project_root_present,
    attributes_string['{_PROJECT_KIND}'] != '' AS project_kind_present,
    (
      project_id_present OR project_name_present OR project_root_present OR project_kind_present
    ) AS project_metadata_present,
    (
      project_id_present AND project_name_present AND project_root_present AND project_kind_present
    ) AS complete_project_metadata
SELECT
    trace_id,
    coalesce(
      nullIf(anyIf(attributes_string['turn.id'], attributes_string['turn.id'] != ''), ''),
      nullIf(anyIf(attributes_string['turn_id'], attributes_string['turn_id'] != ''), '')
    ) AS turn_id,
    nullIf(anyIf(attributes_string['thread.id'], attributes_string['thread.id'] != ''), '')
      AS thread_id,
    multiIf(
      uniqExactIf(
        attributes_string['session.id'],
        serviceName = 'codex-app-server' AND attributes_string['session.id'] != ''
      ) = 1,
      anyIf(
        attributes_string['session.id'],
        serviceName = 'codex-app-server' AND attributes_string['session.id'] != ''
      ),
      NULL
    ) AS session_id,
    multiIf(
      uniqExactIf(
        attributes_string['session.id'],
        serviceName = 'codex-app-server' AND attributes_string['session.id'] != ''
      ) = 1,
      'codex-app-server',
      NULL
    ) AS producer,
    nullIf(anyIf(attributes_string['{_PROJECT_ID}'], complete_project_metadata), '')
      AS project_id,
    nullIf(anyIf(attributes_string['{_PROJECT_NAME}'], complete_project_metadata), '')
      AS project_name,
    nullIf(anyIf(attributes_string['{_PROJECT_ROOT}'], complete_project_metadata), '')
      AS project_root,
    nullIf(anyIf(attributes_string['{_PROJECT_KIND}'], complete_project_metadata), '')
      AS project_kind,
    multiIf(
      countIf(project_metadata_present) = 0,
      'absent',
      countIf(complete_project_metadata) = countIf(project_metadata_present)
        AND uniqExactIf(
          tuple(
            attributes_string['{_PROJECT_ID}'],
            attributes_string['{_PROJECT_NAME}'],
            attributes_string['{_PROJECT_ROOT}'],
            attributes_string['{_PROJECT_KIND}']
          ),
          complete_project_metadata
        ) = 1,
      'complete',
      'invalid'
    ) AS project_metadata_state,
    min(timestamp) AS started_at,
    max(timestamp) AS ended_at,
    multiIf(
      uniqExactIf(
        attributes_string['session.id'],
        serviceName = 'codex-app-server' AND attributes_string['session.id'] != ''
      ) = 1,
      minIf(timestamp, serviceName = 'codex-app-server'),
      NULL
    ) AS source_correlation_started_at,
    multiIf(
      uniqExactIf(
        attributes_string['session.id'],
        serviceName = 'codex-app-server' AND attributes_string['session.id'] != ''
      ) = 1,
      maxIf(timestamp, serviceName = 'codex-app-server'),
      NULL
    ) AS source_correlation_ended_at,
    sumIf(attributes_number['codex.usage.total_tokens'],
          mapContains(attributes_number, 'codex.usage.total_tokens')) AS total_tokens,
    countIf(attributes_string['tool_name'] != '') AS tool_calls
FROM signoz_traces.distributed_signoz_index_v3
WHERE timestamp BETWEEN {{start:DateTime64(9)}} AND {{end:DateTime64(9)}}
  AND ts_bucket_start BETWEEN {{start_bucket:UInt64}} AND {{end_bucket:UInt64}}
  AND serviceName IN ('codex_cli_rs', 'codex-app-server')
  AND (
    name IN ('run_sampling_request', 'session_task.turn', 'turn/start', 'turn/steer',
             'turn/interrupt', 'handle_responses')
    OR mapContains(attributes_number, 'codex.usage.total_tokens')
    OR mapContains(attributes_string, 'tool_name')
  )
GROUP BY trace_id
ORDER BY started_at, trace_id
""".strip()


RETENTION_PROOF_QUERY = r"""
SELECT
  (SELECT count() > 0 FROM signoz_logs.distributed_logs_v2
   WHERE timestamp <= {start_ns:UInt64}
     AND ts_bucket_start <= {start_bucket:UInt64}
     AND resource.`service.name`::String IN ('codex_cli_rs', 'codex-app-server'))
  AND
  (SELECT count() > 0 FROM signoz_traces.distributed_signoz_index_v3
   WHERE timestamp <= {start:DateTime64(9)}
     AND ts_bucket_start <= {start_bucket:UInt64}
     AND serviceName IN ('codex_cli_rs', 'codex-app-server')) AS retained
""".strip()


_HYDRATION_SELECT = r"""
SELECT
    timestamp,
    id,
    trace_id,
    span_id,
    attributes_string['event.name'] AS event_name,
    attributes_string['call_id'] AS call_id,
    attributes_string['tool_name'] AS tool_name,
    attributes_string['arguments'] AS arguments,
    attributes_string['args'] AS args,
    attributes_string['argv'] AS argv,
    attributes_string['output'] AS assistant_output,
    attributes_string['error.message'] AS error_message,
    attributes_string['outcome'] AS outcome,
    attributes_string['diagnostic_code'] AS diagnostic_code,
    attributes_string['success'] AS success_string,
    if(mapContains(attributes_bool, 'success'), attributes_bool['success'], NULL) AS success_bool,
    if(mapContains(attributes_number, 'http.response.status_code'),
       toInt64(attributes_number['http.response.status_code']), NULL) AS status_code,
    if(mapContains(attributes_number, 'exit_code'),
       toInt64(attributes_number['exit_code']), NULL) AS exit_code
FROM signoz_logs.distributed_logs_v2
WHERE {predicate}
  AND timestamp > {start_ns:UInt64}
  AND timestamp <= {end_ns:UInt64}
  AND ts_bucket_start BETWEEN {start_bucket:UInt64} AND {end_bucket:UInt64}
  AND resource.`service.name`::String IN ('codex_cli_rs', 'codex-app-server')
ORDER BY timestamp, id
""".strip()

HydrationIdentityKind = Literal["log_id", "trace_id", "call_id"]


HYDRATION_QUERIES: Mapping[HydrationIdentityKind, str] = {
    "log_id": _HYDRATION_SELECT.replace("{predicate}", "id IN ({identifiers:Array(String)})"),
    "trace_id": _HYDRATION_SELECT.replace(
        "{predicate}", "trace_id IN ({identifiers:Array(String)})"
    ),
    "call_id": _HYDRATION_SELECT.replace(
        "{predicate}", "attributes_string['call_id'] IN ({identifiers:Array(String)})"
    ),
}


class SourceError(RuntimeError):
    """Source execution or validation failed."""


@dataclass(frozen=True, slots=True)
class LogRow:
    timestamp_ns: int
    log_id: str
    trace_id: str | None
    span_id: str | None
    event_name: str
    conversation_id: str | None
    call_id: str | None
    tool_name: str | None
    success_string: str | None
    success_bool: bool | None
    duration_ms: float | None
    status_code: int | None
    decision: str | None
    decision_source: str | None
    input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    prompt_length: int | None


@dataclass(frozen=True, slots=True)
class TraceRow:
    trace_id: str
    turn_id: str | None
    thread_id: str | None
    project_id: str | None
    project_name: str | None
    project_root: str | None
    project_kind: str | None
    started_at: datetime
    ended_at: datetime
    total_tokens: int
    tool_calls: int
    conversation_id: str | None = None
    session_id: str | None = None
    producer: str | None = None
    source_correlation_started_at: datetime | None = None
    source_correlation_ended_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class HydrationRow:
    """Allowlisted raw context for a previously shortlisted candidate."""

    timestamp_ns: int
    log_id: str
    trace_id: str | None
    span_id: str | None
    event_name: str
    call_id: str | None
    tool_name: str | None
    arguments: str | None
    args: str | None
    argv: str | None
    assistant_output: str | None
    error_message: str | None
    outcome: str | None
    diagnostic_code: str | None
    success_string: str | None
    success_bool: bool | None
    status_code: int | None
    exit_code: int | None


def _optional_text(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise SourceError("optional text value must be a string")
    return value


def _optional_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        timestamp = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise SourceError("trace correlation timestamps must be ISO-8601 values") from exc
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, str):
        if re.fullmatch(r"(?:0|[1-9][0-9]*)", value, re.ASCII) is None:
            raise SourceError("expected unsigned integer text or null")
        return int(value)
    if isinstance(value, bool) or not isinstance(value, int):
        raise SourceError(f"expected integer or null, got {type(value).__name__}")
    return value


def parse_duration_ms(value: object) -> float | None:
    """Parse the installed string attribute without accepting ambiguous values."""

    if value is None or value == "":
        return None
    if not isinstance(value, str) or _DURATION_MS.fullmatch(value) is None:
        raise SourceError("duration_ms must be a non-negative decimal string")
    try:
        parsed = float(value)
    except (OverflowError, ValueError) as exc:
        raise SourceError("duration_ms must be a finite decimal string") from exc
    if not math.isfinite(parsed):
        raise SourceError("duration_ms must be a finite decimal string")
    return parsed


def _clickhouse_string_array(values: Sequence[str]) -> str:
    if any(not isinstance(value, str) or value == "" for value in values):
        raise ValueError("ClickHouse identifiers must be non-empty strings")
    escaped = (value.replace("\\", "\\\\").replace("'", "\\'") for value in values)
    return "[" + ",".join(f"'{value}'" for value in escaped) + "]"


def _clickhouse_datetime64(value: datetime) -> str:
    utc = value.astimezone(UTC)
    return utc.strftime("%Y-%m-%d %H:%M:%S.%f") + "000"


def parse_log_row(data: Mapping[str, object]) -> LogRow:
    success_bool = data.get("success_bool")
    if success_bool is not None and not isinstance(success_bool, bool):
        raise SourceError("success_bool must be Boolean or null")
    timestamp = _optional_int(data.get("timestamp"))
    if timestamp is None or timestamp < 0:
        raise SourceError("timestamp must be an unsigned integer")
    log_id = _optional_text(data.get("id"))
    event_name = _optional_text(data.get("event_name")) or ""
    if log_id is None:
        raise SourceError("id is required")
    return LogRow(
        timestamp_ns=timestamp,
        log_id=log_id,
        trace_id=_optional_text(data.get("trace_id")),
        span_id=_optional_text(data.get("span_id")),
        event_name=event_name,
        conversation_id=_optional_text(data.get("conversation_id")),
        call_id=_optional_text(data.get("call_id")),
        tool_name=_optional_text(data.get("tool_name")),
        success_string=_optional_text(data.get("success_string")),
        success_bool=success_bool,
        duration_ms=parse_duration_ms(data.get("duration_ms")),
        status_code=_optional_int(data.get("status_code")),
        decision=_optional_text(data.get("decision")),
        decision_source=_optional_text(data.get("decision_source")),
        input_tokens=_optional_int(data.get("input_tokens")),
        output_tokens=_optional_int(data.get("output_tokens")),
        reasoning_tokens=_optional_int(data.get("reasoning_tokens")),
        prompt_length=_optional_int(data.get("prompt_length")),
    )


def parse_trace_row(data: Mapping[str, object]) -> TraceRow:
    trace_id = _optional_text(data.get("trace_id"))
    if trace_id is None:
        raise SourceError("trace_id is required")
    project_metadata_state = _optional_text(data.get("project_metadata_state"))
    if project_metadata_state not in _PROJECT_METADATA_STATES:
        raise SourceError("project metadata state is required")
    if project_metadata_state == "invalid":
        raise SourceError("agent project metadata is invalid")
    try:
        started = datetime.fromisoformat(str(data["started_at"]))
        ended = datetime.fromisoformat(str(data["ended_at"]))
    except (KeyError, ValueError) as exc:
        raise SourceError("trace timestamps must be ISO-8601 values") from exc
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    if ended.tzinfo is None:
        ended = ended.replace(tzinfo=UTC)
    started = started.astimezone(UTC)
    ended = ended.astimezone(UTC)
    if ended < started:
        raise SourceError("trace timestamps must be ordered")
    source_correlation_started_at = _optional_timestamp(data.get("source_correlation_started_at"))
    source_correlation_ended_at = _optional_timestamp(data.get("source_correlation_ended_at"))
    if (source_correlation_started_at is None) != (source_correlation_ended_at is None):
        raise SourceError("trace correlation timestamps must be present together")
    if (
        source_correlation_started_at is not None
        and source_correlation_ended_at is not None
        and source_correlation_ended_at < source_correlation_started_at
    ):
        raise SourceError("trace correlation timestamps must be ordered")
    total_tokens = _optional_int(data.get("total_tokens"))
    tool_calls = _optional_int(data.get("tool_calls"))
    if total_tokens is None or tool_calls is None or total_tokens < 0 or tool_calls < 0:
        raise SourceError("trace counters must be non-negative integers")
    project_id = _optional_text(data.get("project_id"))
    project_name = _optional_text(data.get("project_name"))
    project_root = _optional_text(data.get("project_root"))
    project_kind = _optional_text(data.get("project_kind"))
    project_attributes = (project_id, project_name, project_root, project_kind)
    if project_metadata_state == "absent" and any(project_attributes):
        raise SourceError("absent project metadata cannot contain project attributes")
    if project_metadata_state == "complete" and not all(project_attributes):
        raise SourceError("complete project metadata must include every project attribute")
    if any(project_attributes) and not all(project_attributes):
        raise SourceError("agent project attributes must be present together")
    if project_kind is not None and project_kind not in _PROJECT_KIND_VALUES:
        raise SourceError("agent.project.kind must be git or non_git")
    if project_root is not None:
        project_path = Path(project_root)
        if not project_path.is_absolute() or project_path.as_posix() != project_root:
            raise SourceError("agent.project.root must be a normalized absolute path")
    return TraceRow(
        trace_id=trace_id,
        turn_id=_optional_text(data.get("turn_id")),
        thread_id=_optional_text(data.get("thread_id")),
        project_id=project_id,
        project_name=project_name,
        project_root=project_root,
        project_kind=project_kind,
        started_at=started,
        ended_at=ended,
        total_tokens=total_tokens,
        tool_calls=tool_calls,
        conversation_id=_optional_text(data.get("conversation_id")),
        session_id=_optional_text(data.get("session_id")),
        producer=_optional_text(data.get("producer")),
        source_correlation_started_at=source_correlation_started_at,
        source_correlation_ended_at=source_correlation_ended_at,
    )


def parse_hydration_row(data: Mapping[str, object]) -> HydrationRow:
    success_bool = data.get("success_bool")
    if success_bool is not None and not isinstance(success_bool, bool):
        raise SourceError("success_bool must be Boolean or null")
    timestamp = _optional_int(data.get("timestamp"))
    log_id = _optional_text(data.get("id"))
    event_name = _optional_text(data.get("event_name")) or ""
    if timestamp is None or timestamp < 0 or log_id is None:
        raise SourceError("hydration timestamp and id are required")
    return HydrationRow(
        timestamp_ns=timestamp,
        log_id=log_id,
        trace_id=_optional_text(data.get("trace_id")),
        span_id=_optional_text(data.get("span_id")),
        event_name=event_name,
        call_id=_optional_text(data.get("call_id")),
        tool_name=_optional_text(data.get("tool_name")),
        arguments=_optional_text(data.get("arguments")),
        args=_optional_text(data.get("args")),
        argv=_optional_text(data.get("argv")),
        assistant_output=_optional_text(data.get("assistant_output")),
        error_message=_optional_text(data.get("error_message")),
        outcome=_optional_text(data.get("outcome")),
        diagnostic_code=_optional_text(data.get("diagnostic_code")),
        success_string=_optional_text(data.get("success_string")),
        success_bool=success_bool,
        status_code=_optional_int(data.get("status_code")),
        exit_code=_optional_int(data.get("exit_code")),
    )


class ClickHouseClient:
    """Execute fixed queries through the existing ClickHouse container."""

    def __init__(
        self,
        *,
        docker_context: str,
        container: str = "signoz-clickhouse",
        executable: str = "docker",
    ) -> None:
        if not docker_context or not container or not executable:
            raise ValueError("docker_context, container, and executable are required")
        self._prefix = (executable, "--context", docker_context, "exec", "-i", container)

    def query(self, sql: str, parameters: Mapping[str, str | int]) -> Iterator[dict[str, Any]]:
        expected = set(_PARAMETER.findall(sql))
        supplied = set(parameters)
        if supplied != expected:
            raise SourceError(
                f"query parameter mismatch: missing={sorted(expected - supplied)!r}, "
                f"extra={sorted(supplied - expected)!r}"
            )
        argv: list[str] = [*self._prefix, "clickhouse-client", "--format", "JSONEachRow"]
        argv.extend(f"--param_{name}={parameters[name]}" for name in sorted(parameters))
        argv.extend(("--query", sql))
        try:
            completed = subprocess.run(
                argv,
                text=True,
                capture_output=True,
                check=False,
                timeout=_QUERY_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise SourceError(
                f"ClickHouse query exceeded {_QUERY_TIMEOUT_SECONDS:.0f} second timeout"
            ) from exc
        if completed.returncode != 0:
            lines = [line.strip() for line in completed.stderr.splitlines() if line.strip()]
            diagnostic = next(
                (line for line in lines if "DB::Exception" in line or line.startswith("Code:")),
                lines[-1] if lines else "unknown ClickHouse error",
            )
            raise SourceError(f"ClickHouse query failed: {diagnostic}")
        for line_number, line in enumerate(completed.stdout.splitlines(), 1):
            try:
                decoded = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SourceError(f"invalid JSONEachRow at line {line_number}") from exc
            if not isinstance(decoded, dict):
                raise SourceError(f"JSONEachRow line {line_number} is not an object")
            yield decoded

    def logs(
        self, *, start_ns: int, end_ns: int, start_bucket: int, end_bucket: int
    ) -> Iterator[LogRow]:
        if not (0 <= start_ns < end_ns and 0 <= start_bucket <= end_bucket):
            raise ValueError("invalid log extraction bounds")
        for row in self.query(
            LOG_QUERY,
            {
                "start_ns": start_ns,
                "end_ns": end_ns,
                "start_bucket": start_bucket,
                "end_bucket": end_bucket,
            },
        ):
            yield parse_log_row(row)

    def traces(
        self, *, start: datetime, end: datetime, start_bucket: int, end_bucket: int
    ) -> Iterator[TraceRow]:
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise ValueError("trace bounds must be ordered, timezone-aware datetimes")
        if not 0 <= start_bucket <= end_bucket:
            raise ValueError("invalid trace bucket bounds")
        parameters: Mapping[str, str | int] = {
            "start": _clickhouse_datetime64(start),
            "end": _clickhouse_datetime64(end),
            "start_bucket": start_bucket,
            "end_bucket": end_bucket,
        }
        for row in self.query(TRACE_QUERY, parameters):
            yield parse_trace_row(row)

    def prove_retained_window(self, *, start: datetime, start_ns: int, start_bucket: int) -> None:
        """Fail closed unless both source streams retain data at the requested start."""

        rows = list(
            self.query(
                RETENTION_PROOF_QUERY,
                {
                    "start": _clickhouse_datetime64(start),
                    "start_ns": start_ns,
                    "start_bucket": start_bucket,
                },
            )
        )
        if len(rows) != 1 or rows[0].get("retained") not in (1, True):
            raise SourceError("source retention is not proven for the requested reanalysis window")

    def hydrate(
        self,
        *,
        identity_kind: HydrationIdentityKind,
        identifiers: Sequence[str],
        start_ns: int,
        end_ns: int,
        start_bucket: int,
        end_bucket: int,
    ) -> Iterator[HydrationRow]:
        """Fetch allowlisted raw fields only for explicitly shortlisted identities."""

        if identity_kind not in HYDRATION_QUERIES:
            raise ValueError("unsupported hydration identity kind")
        if not identifiers:
            raise ValueError("hydration identifiers must be non-empty")
        if any(not isinstance(value, str) or value == "" for value in identifiers):
            raise ValueError("hydration identifiers must be non-empty strings")
        unique_identifiers = tuple(dict.fromkeys(identifiers))
        if not (0 <= start_ns < end_ns and 0 <= start_bucket <= end_bucket):
            raise ValueError("invalid hydration bounds")
        parameters: Mapping[str, str | int] = {
            "identifiers": _clickhouse_string_array(unique_identifiers),
            "start_ns": start_ns,
            "end_ns": end_ns,
            "start_bucket": start_bucket,
            "end_bucket": end_bucket,
        }
        for row in self.query(HYDRATION_QUERIES[identity_kind], parameters):
            yield parse_hydration_row(row)


def query_selected_ids(ids: Sequence[str]) -> tuple[str, Mapping[str, str]]:
    """Build an allowlisted hydration predicate without embedding identifier values."""

    if not ids:
        raise ValueError("at least one id is required")
    if any(not isinstance(value, str) or value == "" for value in ids):
        raise ValueError("selected ids must be non-empty strings")
    placeholders = ", ".join(f"{{id_{index}:String}}" for index in range(len(ids)))
    return f"id IN ({placeholders})", {f"id_{index}": value for index, value in enumerate(ids)}

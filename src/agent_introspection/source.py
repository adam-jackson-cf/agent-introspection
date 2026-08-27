"""Fail-closed extraction from the installed SigNoz ClickHouse schema."""

from __future__ import annotations

import json
import math
import re
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Literal

_PARAMETER = re.compile(r"\{([a-z][a-z0-9_]*):[^}]+\}")
_DURATION_MS = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z", re.ASCII)
_QUERY_TIMEOUT_SECONDS = 600.0


CANONICAL_SERVICE_PRODUCERS: Mapping[str, tuple[str, str]] = MappingProxyType(
    {
        "codex-cli": ("codex-cli", "codex-cli"),
        "claude-code": ("claude-code", "claude-code"),
        "omp": ("omp", "omp"),
        "codex_exec": ("codex-cli", "codex-cli"),
        "codex_cli_rs": ("codex-cli", "codex-cli"),
        "codex-app-server": ("codex-app-server", "codex-app-server"),
        "oh-my-pi": ("omp", "omp"),
    }
)

LOG_QUERY = r"""
SELECT
    timestamp,
    id,
    trace_id,
    span_id,
    resource.`service.name`::String AS service_name,
    attributes_string['event.name'] AS event_name,
    attributes_string['conversation.id'] AS conversation_id,
    attributes_string['thread.id'] AS thread_id,
    attributes_string['thread_id'] AS thread_id_legacy,
    attributes_string['gen_ai.conversation.id'] AS gen_ai_conversation_id,
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
WHERE timestamp >= {start_ns:UInt64}
  AND timestamp < {end_ns:UInt64}
  AND ts_bucket_start BETWEEN {start_bucket:UInt64} AND {end_bucket:UInt64}
ORDER BY timestamp, id
""".strip()


TRACE_QUERY = r"""
SELECT
    trace_id,
    arraySort(groupUniqArray(spanID)) AS source_span_ids,
    arraySort(arrayFilter(value -> value != '', groupUniqArray(serviceName))) AS service_names,
    arraySort(groupUniqArrayIf(attributes_string['turn.id'], attributes_string['turn.id'] != ''))
      AS turn_ids,
    arraySort(groupUniqArrayIf(attributes_string['turn_id'], attributes_string['turn_id'] != ''))
      AS legacy_turn_ids,
    arraySort(groupUniqArrayIf(
      attributes_string['thread.id'], attributes_string['thread.id'] != ''
    )) AS thread_ids,
    arraySort(groupUniqArrayIf(
      attributes_string['thread_id'], attributes_string['thread_id'] != ''
    )) AS legacy_thread_ids,
    arraySort(groupUniqArrayIf(
      attributes_string['conversation.id'], attributes_string['conversation.id'] != ''
    )) AS conversation_ids,
    arraySort(groupUniqArrayIf(
      attributes_string['gen_ai.conversation.id'], attributes_string['gen_ai.conversation.id'] != ''
    )) AS gen_ai_conversation_ids,
    min(timestamp) AS started_at,
    max(timestamp) AS ended_at,
    sumIf(
      attributes_number['codex.turn.token_usage.total_tokens'],
      mapContains(attributes_number, 'codex.turn.token_usage.total_tokens')
    ) + sumIf(
      attributes_number['pi.gen_ai.usage.total_tokens'],
      mapContains(attributes_number, 'pi.gen_ai.usage.total_tokens')
    ) AS total_tokens,
    countIf(
      notEmpty(attributes_string['tool_name'])
      OR notEmpty(attributes_string['gen_ai.tool.name'])
    ) AS tool_calls
FROM signoz_traces.distributed_signoz_index_v3
WHERE timestamp >= {start:DateTime64(9)}
  AND timestamp < {end:DateTime64(9)}
  AND ts_bucket_start BETWEEN {start_bucket:UInt64} AND {end_bucket:UInt64}
GROUP BY trace_id
ORDER BY started_at, trace_id
""".strip()


RAW_SOURCE_SESSION_LOG_QUERY = r"""
SELECT
    resource.`service.name`::String AS service_name,
    id AS source_id,
    min(timestamp) AS source_timestamp_ns,
    arraySort(arrayFilter(
        value -> value != '',
        groupUniqArray(attributes_string['session.id'])
    )) AS session_ids,
    arraySort(arrayFilter(
        value -> value != '',
        groupUniqArray(attributes_string['thread.id'])
    )) AS thread_ids,
    arraySort(arrayFilter(
        value -> value != '',
        groupUniqArray(attributes_string['thread_id'])
    )) AS legacy_thread_ids,
    arraySort(arrayFilter(
        value -> value != '',
        groupUniqArray(attributes_string['gen_ai.conversation.id'])
    )) AS gen_ai_conversation_ids
FROM signoz_logs.distributed_logs_v2
WHERE timestamp >= {start_ns:UInt64}
  AND timestamp < {end_ns:UInt64}
  AND ts_bucket_start BETWEEN {start_bucket:UInt64} AND {end_bucket:UInt64}
  AND resource.`service.name`::String IN (
    'codex-cli', 'claude-code', 'omp', 'codex_exec',
    'codex_cli_rs', 'codex-app-server', 'oh-my-pi'
  )
  AND (
    (resource.`service.name`::String = 'claude-code'
      AND mapContains(attributes_string, 'session.id'))
    OR (resource.`service.name`::String IN (
        'codex-cli', 'codex_exec', 'codex_cli_rs', 'codex-app-server'
      )
      AND (
        mapContains(attributes_string, 'thread.id')
        OR mapContains(attributes_string, 'thread_id')
      ))
    OR (resource.`service.name`::String IN ('omp', 'oh-my-pi')
      AND mapContains(attributes_string, 'gen_ai.conversation.id'))
  )
GROUP BY service_name, source_id
ORDER BY source_timestamp_ns, service_name, source_id
""".strip()


RAW_SOURCE_SESSION_TRACE_QUERY = r"""
SELECT
    serviceName AS service_name,
    spanID AS source_id,
    min(timestamp) AS source_timestamp,
    arraySort(arrayFilter(
        value -> value != '',
        groupUniqArray(attributes_string['session.id'])
    )) AS session_ids,
    arraySort(arrayFilter(
        value -> value != '',
        groupUniqArray(attributes_string['thread.id'])
    )) AS thread_ids,
    arraySort(arrayFilter(
        value -> value != '',
        groupUniqArray(attributes_string['thread_id'])
    )) AS legacy_thread_ids,
    arraySort(arrayFilter(
        value -> value != '',
        groupUniqArray(attributes_string['gen_ai.conversation.id'])
    )) AS gen_ai_conversation_ids
FROM signoz_traces.distributed_signoz_index_v3
WHERE timestamp >= {start:DateTime64(9)}
  AND timestamp < {end:DateTime64(9)}
  AND ts_bucket_start BETWEEN {start_bucket:UInt64} AND {end_bucket:UInt64}
  AND serviceName IN (
    'codex-cli', 'claude-code', 'omp', 'codex_exec',
    'codex_cli_rs', 'codex-app-server', 'oh-my-pi'
  )
  AND (
    (serviceName = 'claude-code' AND mapContains(attributes_string, 'session.id'))
    OR (serviceName IN (
        'codex-cli', 'codex_exec', 'codex_cli_rs', 'codex-app-server'
      )
      AND (
        mapContains(attributes_string, 'thread.id')
        OR mapContains(attributes_string, 'thread_id')
      ))
    OR (serviceName IN ('omp', 'oh-my-pi')
      AND mapContains(attributes_string, 'gen_ai.conversation.id'))
  )
GROUP BY service_name, source_id
ORDER BY source_timestamp, service_name, source_id
""".strip()


RAW_SOURCE_WINDOW_ANCHOR_QUERY = r"""
SELECT
  (SELECT min(timestamp) FROM signoz_logs.distributed_logs_v2) AS logs_earliest_ns,
  (SELECT toUnixTimestamp64Nano(min(timestamp))
   FROM signoz_traces.distributed_signoz_index_v3) AS traces_earliest_ns
""".strip()


RETENTION_PROOF_QUERY = r"""
SELECT
  (SELECT count() > 0 FROM signoz_logs.distributed_logs_v2
   WHERE timestamp <= {start_ns:UInt64}
     AND ts_bucket_start <= {start_bucket:UInt64}
     AND resource.`service.name`::String IN (
         'codex_exec', 'codex_cli_rs', 'codex-app-server', 'oh-my-pi'
     ))
  AND
  (SELECT count() > 0 FROM signoz_traces.distributed_signoz_index_v3
   WHERE timestamp <= {start:DateTime64(9)}
     AND ts_bucket_start <= {start_bucket:UInt64}
     AND serviceName IN (
         'codex_exec', 'codex_cli_rs', 'codex-app-server', 'oh-my-pi'
     )) AS retained
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
  AND resource.`service.name`::String IN (
      'codex_exec', 'codex_cli_rs', 'codex-app-server', 'oh-my-pi'
  )
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
    service_name: str | None = None
    thread_id: str | None = None
    legacy_thread_id: str | None = None
    gen_ai_conversation_id: str | None = None
    producer: str | None = None


@dataclass(frozen=True, slots=True)
class TraceRow:
    trace_id: str
    turn_id: str | None
    thread_id: str | None
    started_at: datetime
    ended_at: datetime
    total_tokens: int
    tool_calls: int
    source_span_ids: tuple[str, ...] = ()
    service_names: tuple[str, ...] = ()
    turn_ids: tuple[str, ...] = ()
    legacy_turn_ids: tuple[str, ...] = ()
    thread_ids: tuple[str, ...] = ()
    legacy_thread_ids: tuple[str, ...] = ()
    conversation_ids: tuple[str, ...] = ()
    gen_ai_conversation_ids: tuple[str, ...] = ()
    correlation: SourceActivityCorrelation | None = None
    correlation_status: SourceCorrelationStatus | None = None


@dataclass(frozen=True, slots=True)
class SourceActivityCorrelation:
    """Canonical native source correlation and allowlisted source provenance."""

    producer: str
    producer_surface: str
    correlation_id: str
    source_event_timestamp: datetime
    source_event_ids: tuple[str, ...]
    source_log_ids: tuple[str, ...]
    source_span_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SourceCorrelationStatus:
    """Bounded source-correlation state and provenance for a trace."""

    state: Literal["missing", "conflicting"]
    producer: str
    producer_surface: str
    source_event_timestamp: datetime
    source_span_ids: tuple[str, ...]


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


@dataclass(frozen=True, slots=True)
class SourceSessionRow:
    """One deduplicated, detector-independent bounded source record."""

    source_kind: Literal["log", "trace"]
    source_id: str
    source_timestamp: datetime
    service_name: str
    session_ids: tuple[str, ...]
    thread_ids: tuple[str, ...]
    legacy_thread_ids: tuple[str, ...]
    gen_ai_conversation_ids: tuple[str, ...]

    @property
    def native_session_ids(self) -> tuple[str, ...]:
        """Return only the native IDs authorized for this signal/service contract."""
        if self.service_name == "claude-code":
            identifiers = self.session_ids
        elif self.service_name in {"codex-cli", "codex_exec", "codex_cli_rs", "codex-app-server"}:
            identifiers = (*self.thread_ids, *self.legacy_thread_ids)
        elif self.source_kind == "trace" and self.service_name in {"omp", "oh-my-pi"}:
            identifiers = self.gen_ai_conversation_ids
        else:
            identifiers = ()
        return tuple(sorted(set(identifiers)))

    @property
    def session_status(self) -> Literal["missing", "wrong_field", "exact", "conflicting"]:
        count = len(self.native_session_ids)
        if count == 0:
            return (
                "wrong_field"
                if any(
                    (
                        self.session_ids,
                        self.thread_ids,
                        self.legacy_thread_ids,
                        self.gen_ai_conversation_ids,
                    )
                )
                else "missing"
            )
        return "exact" if count == 1 else "conflicting"


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
        raise SourceError("source event timestamps must be ISO-8601 values") from exc
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)


_PRODUCER_SURFACES: Mapping[str, str] = {
    "codex-cli": "codex-cli",
    "codex-app-server": "codex-app-server",
    "omp": "omp",
}
_PROVENANCE_FIELDS = ("source_event_ids", "source_log_ids", "source_span_ids")


def _provenance_ids(data: Mapping[str, object], field: str) -> tuple[str, ...]:
    value = data.get(field, ())
    if not isinstance(value, (list, tuple)):
        raise SourceError(f"{field} must be an array of non-empty strings")
    identifiers = tuple(value)
    if any(not isinstance(identifier, str) or not identifier for identifier in identifiers):
        raise SourceError(f"{field} must be an array of non-empty strings")
    if identifiers != tuple(sorted(set(identifiers))):
        raise SourceError(f"{field} must contain sorted unique identifiers")
    return identifiers


def parse_source_activity_correlation(
    data: Mapping[str, object],
) -> SourceActivityCorrelation | None:
    """Parse the sole canonical source-correlation representation."""

    fields = ("producer", "producer_surface", "correlation_id", "source_event_timestamp")
    present = tuple(data.get(field) is not None for field in fields)
    if not any(present):
        return None
    if not all(present):
        raise SourceError("source correlation fields must be present together")
    producer = _optional_text(data.get("producer"))
    producer_surface = _optional_text(data.get("producer_surface"))
    correlation_id = _optional_text(data.get("correlation_id"))
    source_event_timestamp = _optional_timestamp(data.get("source_event_timestamp"))
    if producer is None or producer_surface is None:
        raise SourceError("source correlation producer is invalid")
    producer_surface_for_producer = _PRODUCER_SURFACES.get(producer)
    if producer_surface_for_producer is None:
        raise SourceError("source correlation producer is invalid")
    if producer_surface != producer_surface_for_producer:
        raise SourceError("source correlation producer surface is invalid")
    if correlation_id is None:
        raise SourceError("source correlation ID is required")
    if source_event_timestamp is None:
        raise SourceError("source event timestamp is required")
    provenance = tuple(_provenance_ids(data, field) for field in _PROVENANCE_FIELDS)
    if not any(provenance):
        raise SourceError("source correlation requires allowlisted provenance identifiers")
    return SourceActivityCorrelation(
        producer=producer,
        producer_surface=producer_surface,
        correlation_id=correlation_id,
        source_event_timestamp=source_event_timestamp,
        source_event_ids=provenance[0],
        source_log_ids=provenance[1],
        source_span_ids=provenance[2],
    )


def _parse_source_correlation_status(
    data: Mapping[str, object], correlation: SourceActivityCorrelation | None
) -> SourceCorrelationStatus | None:
    value = data.get("correlation_status")
    if value is None:
        return None
    if value == "valid":
        if correlation is None:
            raise SourceError("valid source correlation status requires a correlation")
        return None
    if value == "missing":
        state: Literal["missing", "conflicting"] = "missing"
    elif value == "conflicting":
        state = "conflicting"
    else:
        raise SourceError("source correlation status is invalid")
    if correlation is not None or data.get("correlation_id") is not None:
        raise SourceError("rejected source correlation must not include a correlation ID")
    producer = _optional_text(data.get("producer"))
    producer_surface = _optional_text(data.get("producer_surface"))
    source_event_timestamp = _optional_timestamp(data.get("source_event_timestamp"))
    if (
        producer is None
        or producer_surface is None
        or _PRODUCER_SURFACES.get(producer) != producer_surface
    ):
        raise SourceError("rejected source correlation producer surface is invalid")
    if source_event_timestamp is None:
        raise SourceError("rejected source correlation timestamp is required")
    source_span_ids = _provenance_ids(data, "source_span_ids")
    if not source_span_ids:
        raise SourceError("rejected source correlation requires source span IDs")
    for field in ("source_event_ids", "source_log_ids"):
        if _provenance_ids(data, field):
            raise SourceError("rejected source correlation must only contain source span IDs")
    return SourceCorrelationStatus(
        state=state,
        producer=producer,
        producer_surface=producer_surface,
        source_event_timestamp=source_event_timestamp,
        source_span_ids=source_span_ids,
    )


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


def _string_array(data: Mapping[str, object], field: str) -> tuple[str, ...]:
    value = data.get(field, ())
    if not isinstance(value, (list, tuple)):
        raise SourceError(f"{field} must be an array of non-empty strings")
    values = tuple(value)
    if any(not isinstance(item, str) or not item for item in values):
        raise SourceError(f"{field} must be an array of non-empty strings")
    if values != tuple(sorted(set(values))):
        raise SourceError(f"{field} must contain sorted unique identifiers")
    return values


def _single_raw_identity(*values: tuple[str, ...]) -> str | None:
    identities = tuple(sorted({identity for group in values for identity in group}))
    return identities[0] if len(identities) == 1 else None


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


def parse_source_session_row(
    data: Mapping[str, object], *, source_kind: Literal["log", "trace"]
) -> SourceSessionRow:
    """Parse a raw source record without deriving detector eligibility."""

    source_id = _optional_text(data.get("source_id"))
    service_name = _optional_text(data.get("service_name"))
    if source_id is None or service_name is None:
        raise SourceError("raw source record requires service name and source ID")
    if source_kind == "log":
        timestamp_ns = _optional_int(data.get("source_timestamp_ns"))
        if timestamp_ns is None or timestamp_ns < 0:
            raise SourceError("raw log source timestamp must be an unsigned integer")
        source_timestamp = datetime.fromtimestamp(timestamp_ns / 1_000_000_000, tz=UTC)
    else:
        parsed_timestamp = _optional_timestamp(data.get("source_timestamp"))
        if parsed_timestamp is None:
            raise SourceError("raw trace source timestamp must be ISO-8601")
        source_timestamp = parsed_timestamp
    return SourceSessionRow(
        source_kind=source_kind,
        source_id=source_id,
        source_timestamp=source_timestamp,
        service_name=service_name,
        session_ids=_string_array(data, "session_ids"),
        thread_ids=_string_array(data, "thread_ids"),
        legacy_thread_ids=_string_array(data, "legacy_thread_ids"),
        gen_ai_conversation_ids=_string_array(data, "gen_ai_conversation_ids"),
    )


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
        service_name=_optional_text(data.get("service_name")),
        thread_id=_optional_text(data.get("thread_id")),
        legacy_thread_id=_optional_text(data.get("thread_id_legacy")),
        gen_ai_conversation_id=_optional_text(data.get("gen_ai_conversation_id")),
        producer=_optional_text(data.get("producer")),
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
    try:
        started = datetime.fromisoformat(str(data["started_at"]))
        ended = datetime.fromisoformat(str(data["ended_at"]))
    except (KeyError, ValueError) as exc:
        raise SourceError("trace timestamps must be ISO-8601 values") from exc
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    if ended.tzinfo is None:
        ended = ended.replace(tzinfo=UTC)
    correlation = (
        parse_source_activity_correlation(data)
        if data.get("correlation_status") not in {"missing", "conflicting"}
        else None
    )
    total_tokens = _optional_int(data.get("total_tokens"))
    tool_calls = _optional_int(data.get("tool_calls"))
    if total_tokens is None or tool_calls is None or total_tokens < 0 or tool_calls < 0:
        raise SourceError("trace counters must be non-negative integers")
    turn_ids = _string_array(data, "turn_ids")
    legacy_turn_ids = _string_array(data, "legacy_turn_ids")
    thread_ids = _string_array(data, "thread_ids")
    legacy_thread_ids = _string_array(data, "legacy_thread_ids")
    conversation_ids = _string_array(data, "conversation_ids")
    gen_ai_conversation_ids = _string_array(data, "gen_ai_conversation_ids")
    return TraceRow(
        trace_id=trace_id,
        thread_id=_single_raw_identity(thread_ids, legacy_thread_ids)
        or _optional_text(data.get("thread_id")),
        turn_id=_single_raw_identity(turn_ids, legacy_turn_ids)
        or _optional_text(data.get("turn_id")),
        started_at=started,
        ended_at=ended,
        total_tokens=total_tokens,
        tool_calls=tool_calls,
        source_span_ids=_string_array(data, "source_span_ids"),
        service_names=_string_array(data, "service_names"),
        turn_ids=turn_ids,
        legacy_turn_ids=legacy_turn_ids,
        thread_ids=thread_ids,
        legacy_thread_ids=legacy_thread_ids,
        conversation_ids=conversation_ids,
        gen_ai_conversation_ids=gen_ai_conversation_ids,
        correlation=correlation,
        correlation_status=_parse_source_correlation_status(data, correlation),
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


def _window_buckets(*, start_ns: int, end_ns: int) -> tuple[int, int]:
    """Return safe partition bounds for a nanosecond half-open source window."""
    return max(0, start_ns // 1_000_000_000 - 1_800), end_ns // 1_000_000_000


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

    def source_sessions(
        self, *, start: datetime, end: datetime, start_ns: int, end_ns: int
    ) -> Iterator[SourceSessionRow]:
        """Yield the full detector-independent source population in one window."""

        if start.tzinfo is None or end.tzinfo is None or start >= end or not 0 <= start_ns < end_ns:
            raise ValueError("invalid raw source extraction bounds")
        start_bucket, end_bucket = _window_buckets(start_ns=start_ns, end_ns=end_ns)
        for row in self.query(
            RAW_SOURCE_SESSION_LOG_QUERY,
            {
                "start_ns": start_ns,
                "end_ns": end_ns,
                "start_bucket": start_bucket,
                "end_bucket": end_bucket,
            },
        ):
            yield parse_source_session_row(row, source_kind="log")
        for row in self.query(
            RAW_SOURCE_SESSION_TRACE_QUERY,
            {
                "start": _clickhouse_datetime64(start),
                "end": _clickhouse_datetime64(end),
                "start_bucket": start_bucket,
                "end_bucket": end_bucket,
            },
        ):
            yield parse_source_session_row(row, source_kind="trace")

    def logs(self, *, start_ns: int, end_ns: int) -> Iterator[LogRow]:
        if not 0 <= start_ns < end_ns:
            raise ValueError("invalid log extraction bounds")
        start_bucket, end_bucket = _window_buckets(start_ns=start_ns, end_ns=end_ns)
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

    def traces(self, *, start: datetime, end: datetime) -> Iterator[TraceRow]:
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise ValueError("trace bounds must be ordered, timezone-aware datetimes")
        start_ns = int(start.astimezone(UTC).timestamp() * 1_000_000_000)
        end_ns = int(end.astimezone(UTC).timestamp() * 1_000_000_000)
        start_bucket, end_bucket = _window_buckets(start_ns=start_ns, end_ns=end_ns)
        parameters: Mapping[str, str | int] = {
            "start": _clickhouse_datetime64(start),
            "end": _clickhouse_datetime64(end),
            "start_bucket": start_bucket,
            "end_bucket": end_bucket,
        }
        for row in self.query(TRACE_QUERY, parameters):
            yield parse_trace_row(row)

    def raw_source_window_anchor(self) -> tuple[int, int]:
        """Return earliest timestamps for both raw source streams."""

        rows = list(self.query(RAW_SOURCE_WINDOW_ANCHOR_QUERY, {}))
        if len(rows) != 1:
            raise SourceError("raw source window anchor is unavailable")

        def timestamp_ns(value: object) -> int:
            try:
                return int(str(value))
            except (TypeError, ValueError) as exc:
                timestamp = _optional_timestamp(value)
                if timestamp is None:
                    raise SourceError("raw source window anchor is unavailable") from exc
                return int(timestamp.timestamp() * 1_000_000_000)

        try:
            logs = timestamp_ns(rows[0]["logs_earliest_ns"])
            traces = timestamp_ns(rows[0]["traces_earliest_ns"])
        except KeyError as exc:
            raise SourceError("raw source window anchor requires both source streams") from exc
        if logs < 0 or traces < 0:
            raise SourceError("raw source window anchor is invalid")
        return logs, traces

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
            raise SourceError("source retention is not proven for the requested retained window")

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

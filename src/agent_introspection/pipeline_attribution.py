"""Fail-closed remote attribution measurements for Pipeline health."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any, cast

from agent_introspection.pipeline_contracts import PipelineMetric, PipelinePanel, Scalar
from agent_introspection.pipeline_events import (
    ImmutableEvent,
    ManifestCache,
    decode_immutable_event,
    read_event_ids,
)
from agent_introspection.pipeline_integrity import require_canonical_integrity
from agent_introspection.pipeline_projection import (
    PipelineWindow,
    SnapshotCache,
    query_pipeline_snapshots,
)
from agent_introspection.source import (
    CANONICAL_SERVICE_PRODUCERS,
    SourceError,
    SourceSessionRow,
    query_raw_native_sources,
)
from agent_introspection.telemetry import (
    CANONICAL_ACTIVITY_EVENT_NAME,
    CANONICAL_ACTIVITY_PAYLOAD_SCHEMA_VERSION,
    OPERATIONAL_SCOPE,
    EventQueryClient,
)

_INTERVAL_CAPABLE = {"omp", "codex-app-server"}
_ACCEPTED_ID = re.compile(r"[a-f0-9]{64}\Z")

_ACTIVITY_SQL = """
WITH candidates AS (
  SELECT DISTINCT attributes_string['activity.id'] AS activity_id
  FROM agent_introspection.events
  WHERE resource.`service.name`::String = 'agent-introspection'
    AND attributes_string['event.name'] = 'introspection.activity.version.recorded'
    AND timestamp > {start:UInt64} AND timestamp <= {end:UInt64}
), canonical_activity_integrity AS (
  SELECT
    timestamp AS source_time_ns,
    timestamp AS timestamp,
    body,
    attributes_string AS strings,
    mapApply((key, value) -> (key, toString(reinterpretAsUInt64(value))),
             attributes_number) AS number_bits,
    attributes_bool AS booleans,
    attributes_string['event.id'] AS event_id,
    attributes_string['activity.id'] AS activity_id,
    attributes_number['activity.version'] AS version,
    attributes_number['activity.payload_schema_version'] AS schema_version,
    attributes_string['activity.producer'] AS producer,
    attributes_string['activity.producer_surface'] AS surface,
    attributes_string['activity.correlation_id'] AS correlation_id,
    attributes_string['activity.detector.id'] AS detector_id,
    attributes_number['activity.detector.version'] AS detector_version,
    attributes_number['activity.normalization.version'] AS normalization_version,
    attributes_string['activity.attribution.state'] AS attribution_state,
    attributes_string['activity.attribution.method'] AS attribution_method,
    attributes_string['activity.attribution.evidence_id'] AS evidence_id,
    attributes_string['activity.attribution.reason_code'] AS reason_code,
    attributes_string['activity.attribution.project_identity_id'] AS project_identity_id,
    attributes_string['agent.project.id'] AS project_id,
    attributes_string['agent.project.name'] AS project_name,
    attributes_string['event.name'] AS event_name
  FROM agent_introspection.events
  WHERE resource.`service.name`::String = 'agent-introspection'
    AND attributes_string['event.name'] = 'introspection.activity.version.recorded'
    AND attributes_string['activity.id'] IN candidates
)
SELECT * FROM canonical_activity_integrity
ORDER BY activity_id, version, event_id
"""

_LIFECYCLE_SQL = """
SELECT timestamp, body, attributes_string AS strings,
       mapApply((key, value) -> (key, toString(reinterpretAsUInt64(value))),
                attributes_number) AS number_bits,
       attributes_bool AS booleans
FROM agent_introspection.events
WHERE resource.`service.name`::String = 'agent-introspection'
  AND timestamp <= {evaluated_at:UInt64}
  AND attributes_string['event.name'] IN (
    'introspection.session_context.interval.recorded',
    'introspection.session_context.superseded',
    'introspection.session_context.population'
  )
ORDER BY timestamp, attributes_string['event.id']
"""


@dataclass(frozen=True)
class _PanelContents:
    metrics: list[PipelineMetric]
    columns: list[str]
    rows: list[list[str | int | float | None]]
    reasons: list[str]


@dataclass(frozen=True)
class _MetricContents:
    value: int | float | None
    numerator: int | None = None
    denominator: int | None = None
    sample_count: int | None = None


@dataclass(frozen=True)
class _Interval:
    producer: str
    surface: str
    session: str
    opening_id: str
    start_ns: int
    end_ns: int | None
    version: int
    project_id: str
    project_name: str

    @property
    def key(self) -> tuple[str, str, str]:
        return self.producer, self.surface, self.session

    def contains(self, source_ns: int) -> bool:
        return self.start_ns <= source_ns and (self.end_ns is None or source_ns < self.end_ns)


def _panel(state: str, population: str, basis: str, contents: _PanelContents) -> PipelinePanel:
    return {
        "state": cast(Any, state),
        "population": population,
        "timeBasis": basis,
        "rangeOperator": (
            "start < source timestamp <= end; lifecycle containment start <= source < end"
        ),
        "metrics": contents.metrics,
        "columns": contents.columns,
        "rows": contents.rows,
        "series": [],
        "reasons": contents.reasons,
        "provenance": {
            "source": "remote immutable OTLP",
            "query": "EventQueryClient",
            "schema": "canonical-activity-v2",
        },
    }


def _metric(label: str, unit: str, contents: _MetricContents) -> PipelineMetric:
    return {
        "label": label,
        "value": contents.value,
        "unit": unit,
        "numerator": contents.numerator,
        "denominator": contents.denominator,
        "sampleCount": contents.sample_count,
    }


def _text(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    return value if isinstance(value, str) else ""


def _integer(row: Mapping[str, Any], key: str) -> int:
    value = row.get(key)
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        value = int(value)
    if isinstance(value, float) and value.is_integer() and 0 <= value <= 2**53 - 1:
        value = int(value)
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 2**64
        else -1
    )


def _event_id(activity_id: str, version: int, schema: int, name: str) -> str:
    return hashlib.sha256(f"{activity_id}\x1f{version}\x1f{schema}\x1f{name}".encode()).hexdigest()


def _history_metadata_is_complete(row: Mapping[str, Any]) -> bool:
    return all(
        (
            _text(row, "activity_id"),
            _text(row, "event_id"),
            _text(row, "event_name"),
            _text(row, "producer"),
            _text(row, "surface"),
            _text(row, "correlation_id"),
            _text(row, "detector_id"),
            _text(row, "attribution_state"),
            _text(row, "attribution_method"),
            _text(row, "project_id"),
            _text(row, "project_name"),
        )
    ) and all(
        _integer(row, name) > 0
        for name in ("version", "schema_version", "detector_version", "normalization_version")
    )


def _history_state_is_valid(row: Mapping[str, Any]) -> bool:
    state = _text(row, "attribution_state")
    if state == "resolved":
        return _text(row, "project_id") != "unresolved" and not _text(row, "reason_code")
    return (
        state == "unresolved"
        and _text(row, "project_id") == "unresolved"
        and bool(_text(row, "reason_code"))
        and not _text(row, "evidence_id")
    )


def _history_row_error(row: Mapping[str, Any]) -> str:
    if not _history_metadata_is_complete(row):
        return "missing required immutable canonical metadata"
    activity_id, version = _text(row, "activity_id"), _integer(row, "version")
    schema, name = _integer(row, "schema_version"), _text(row, "event_name")
    if (
        name != CANONICAL_ACTIVITY_EVENT_NAME
        or schema != CANONICAL_ACTIVITY_PAYLOAD_SCHEMA_VERSION
        or _text(row, "event_id") != _event_id(activity_id, version, schema, name)
    ):
        return "deterministic canonical event identity mismatch"
    if not _history_state_is_valid(row):
        return "attribution state contract is invalid"
    return ""


def _history_population_error(
    versions: set[int], source_times: set[int], project_names: dict[str, set[str]]
) -> str:
    if source_times == {-1} or len(source_times) != 1:
        return "canonical versions disagree on source timestamp"
    if versions != set(range(1, len(versions) + 1)):
        return "canonical version continuity failed"
    if any(len(names) != 1 for names in project_names.values()):
        return "canonical project ID has conflicting names"
    return ""


def _valid_history(rows: list[Mapping[str, Any]]) -> tuple[bool, str]:
    if not rows:
        return False, "empty canonical history"
    versions: set[int] = set()
    event_ids: set[str] = set()
    source_times: set[int] = set()
    project_names: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        reason = _history_row_error(row)
        if reason:
            return False, reason
        version, event_id = _integer(row, "version"), _text(row, "event_id")
        if version in versions or event_id in event_ids:
            return False, "duplicate canonical event or version"
        versions.add(version)
        event_ids.add(event_id)
        source_times.add(_integer(row, "source_time_ns"))
        project_names[_text(row, "project_id")].add(_text(row, "project_name"))
    reason = _history_population_error(versions, source_times, project_names)
    return not reason, reason


def _percentile(values: list[float], pct: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[(len(ordered) * pct + 99) // 100 - 1]


def _immediate_transitions(
    histories: Mapping[str, list[Mapping[str, Any]]], candidates: set[str]
) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    transitions: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for identity in sorted(candidates):
        history = sorted(histories[identity], key=lambda row: _integer(row, "version"))
        for prior, current in pairwise(history):
            if (
                _text(prior, "attribution_state") == "unresolved"
                and _text(current, "attribution_state") == "resolved"
            ):
                transitions.append((prior, current))
    return transitions


def _activity_rows(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    require_canonical_integrity(rows)
    # Canonical activity hashes were validated above; they are not DerivedEvent UUIDs.
    events = {event.text("event.id"): event for event in map(decode_immutable_event, rows)}
    result: list[dict[str, Any]] = []
    for event in events.values():
        if event.text("event.name") != CANONICAL_ACTIVITY_EVENT_NAME:
            raise ValueError("invalid canonical activity envelope")
        result.append(
            {
                "source_time_ns": event.timestamp_ns,
                "timestamp": event.timestamp_ns,
                "event_id": event.text("event.id"),
                "activity_id": event.text("activity.id"),
                "version": event.numbers.get("activity.version", 0),
                "schema_version": event.numbers.get("activity.payload_schema_version", 0),
                "producer": event.strings.get("activity.producer", ""),
                "surface": event.strings.get("activity.producer_surface", ""),
                "correlation_id": event.strings.get("activity.correlation_id", ""),
                "detector_id": event.strings.get("activity.detector.id", ""),
                "detector_version": event.numbers.get("activity.detector.version", 0),
                "normalization_version": event.numbers.get("activity.normalization.version", 0),
                "attribution_state": event.strings.get("activity.attribution.state", ""),
                "attribution_method": event.strings.get("activity.attribution.method", ""),
                "evidence_id": event.strings.get("activity.attribution.evidence_id", ""),
                "reason_code": event.strings.get("activity.attribution.reason_code", ""),
                "project_identity_id": event.strings.get(
                    "activity.attribution.project_identity_id", ""
                ),
                "project_id": event.strings.get("agent.project.id", ""),
                "project_name": event.strings.get("agent.project.name", ""),
                "event_name": event.text("event.name"),
            }
        )
    return result


def _activity_histories(
    rows: list[Mapping[str, Any]],
) -> tuple[dict[str, list[Mapping[str, Any]]], list[str]]:
    histories: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    deliveries: dict[tuple[str, int], Mapping[str, Any]] = {}
    invalid: list[str] = []
    for row in rows:
        key = _text(row, "activity_id"), _integer(row, "version")
        prior = deliveries.get(key)
        if prior is not None:
            if dict(prior) != dict(row):
                invalid.append("canonical repeated delivery diverges")
            continue
        deliveries[key] = row
        histories[key[0]].append(row)
    return histories, invalid


def _activity_panels(
    rows: list[Mapping[str, Any]], start_ns: int, end_ns: int
) -> tuple[PipelinePanel, PipelinePanel, PipelinePanel, PipelinePanel]:
    activity_rows = cast(list[Mapping[str, Any]], _activity_rows(rows))
    histories, invalid = _activity_histories(activity_rows)
    candidates = {
        identity
        for identity, history in histories.items()
        if any(start_ns < _integer(row, "source_time_ns") <= end_ns for row in history)
    }
    for identity in candidates:
        valid, reason = _valid_history(histories[identity])
        if not valid:
            invalid.append(reason)
    names: dict[str, set[str]] = defaultdict(set)
    for identity in candidates:
        for row in histories[identity]:
            names[_text(row, "project_id")].add(_text(row, "project_name"))
    if any(len(values) != 1 for values in names.values()):
        invalid.append("canonical project ID has conflicting names in population")
    if invalid:
        failure = _panel(
            "Integrity failure",
            "Canonical activity candidate histories",
            "immutable source timestamp",
            _PanelContents([], [], [], sorted(set(invalid))),
        )
        return failure, failure.copy(), failure.copy(), failure.copy()
    latest = [
        max(histories[identity], key=lambda row: _integer(row, "version"))
        for identity in sorted(candidates)
    ]
    eligible = len(latest)
    resolved = sum(_text(row, "attribution_state") == "resolved" for row in latest)
    p7 = _panel(
        "Data" if eligible else "No data",
        "Latest valid canonical activities",
        "immutable source timestamp",
        _PanelContents(
            [
                _metric(
                    "Eligible activities",
                    "activities",
                    _MetricContents(eligible, sample_count=eligible),
                ),
                _metric(
                    "Attributed activities",
                    "activities",
                    _MetricContents(resolved, resolved, eligible),
                ),
                _metric(
                    "Unresolved activities",
                    "activities",
                    _MetricContents(eligible - resolved, eligible - resolved, eligible),
                ),
                _metric(
                    "Attribution coverage",
                    "percent",
                    _MetricContents(
                        100 * resolved / eligible if eligible else None, resolved, eligible
                    ),
                ),
            ],
            [],
            [],
            [] if eligible else ["No data for the selected filters and range"],
        ),
    )
    diagnostics: dict[tuple[str, str, str, str, str], int] = defaultdict(int)
    for row in latest:
        diagnostics[
            (
                _text(row, "producer"),
                _text(row, "surface"),
                _text(row, "attribution_method"),
                _text(row, "attribution_state"),
                _text(row, "reason_code") or "none",
            )
        ] += 1
    p8 = _panel(
        "Data" if eligible else "No data",
        "Same complete P7 eligible activity population",
        "immutable source timestamp",
        _PanelContents(
            [
                _metric(
                    "Diagnostic population",
                    "activities",
                    _MetricContents(eligible, sample_count=eligible),
                )
            ],
            ["Producer", "Surface", "Method", "State", "Reason", "Activities"],
            [
                [*key, count]
                for key, count in sorted(diagnostics.items(), key=lambda item: (-item[1], item[0]))[
                    :50
                ]
            ],
            [] if eligible else ["No data for the selected filters and range"],
        ),
    )
    transitions = _immediate_transitions(histories, candidates)
    ever_unresolved = sum(
        any(_text(row, "attribution_state") == "unresolved" for row in histories[identity])
        for identity in candidates
    )
    counts: dict[tuple[str, str, str, str, str], int] = defaultdict(int)
    for prior, current in transitions:
        counts[
            (
                _text(current, "producer"),
                _text(current, "surface"),
                _text(current, "attribution_method"),
                _text(prior, "reason_code"),
                _text(current, "project_name"),
            )
        ] += 1
    transition_contents = _PanelContents(
        [
            _metric(
                "Late-context reconciliations",
                "activities",
                _MetricContents(len(transitions), len(transitions), len(candidates)),
            ),
            _metric(
                "Reconciliation rate",
                "percent",
                _MetricContents(
                    100 * len(transitions) / ever_unresolved if ever_unresolved else None,
                    len(transitions),
                    ever_unresolved,
                ),
            ),
        ],
        ["Producer", "Surface", "Method", "Prior reason", "Project", "Transitions"],
        [
            [*key, count]
            for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:50]
        ],
        [] if candidates else ["No data for the selected filters and range"],
    )
    p9 = _panel(
        "Data" if candidates else "No data",
        "Complete all-version canonical histories for source-time candidates",
        "immutable source timestamp",
        transition_contents,
    )
    evidence = _panel(
        "Data" if transitions else "No data",
        "Deterministic resolved-version evidence, redacted",
        "immutable source timestamp",
        transition_contents
        if transitions
        else _PanelContents(
            [],
            transition_contents.columns,
            [],
            ["No immediate unresolved-to-resolved transition in the selected cohort"],
        ),
    )
    return p7, p8, p9, evidence


class _IncompleteLifecycleError(ValueError):
    """A required replacement authority projection has not arrived."""


@dataclass(frozen=True, slots=True)
class _Supersession:
    original: str
    replacement: str
    native_key: tuple[str, str, str]
    original_kind: str
    replacement_kind: str


def _serialized_time_ns(value: str) -> int:
    instant = datetime.fromisoformat(value)
    if instant.tzinfo is None:
        raise ValueError("lifecycle time lacks timezone")
    delta = instant.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1000


def _interval_end(event: ImmutableEvent) -> int | None:
    state = event.text("interval.state")
    if state == "closed":
        end = event.nanoseconds("interval.end_ns")
        if _serialized_time_ns(event.text("interval.ended_at")) != end:
            raise ValueError("lifecycle end differs from fingerprint input")
        return end
    if state != "open" or any(
        key in event.strings or key in event.numbers
        for key in ("interval.end_ns", "interval.ended_at")
    ):
        raise ValueError("lifecycle open interval has invalid end metadata")
    return None


def _read_interval(event: ImmutableEvent) -> _Interval:
    interval = _Interval(
        event.text("producer"),
        event.text("producer.surface"),
        event.text("session.id"),
        event.text("interval.opening_event_id"),
        event.nanoseconds("interval.start_ns"),
        _interval_end(event),
        event.nanoseconds("interval.version"),
        event.text("agent.project.id"),
        event.text("agent.project.name"),
    )
    expected_entity = f"lifecycle:{interval.producer}:{interval.session}:{interval.opening_id}"
    if (
        interval.producer not in _INTERVAL_CAPABLE
        or interval.surface != interval.producer
        or event.text("entity.id") != expected_entity
        or _ACCEPTED_ID.fullmatch(interval.opening_id) is None
        or _ACCEPTED_ID.fullmatch(interval.project_id) is None
        or event.count("entity.version") != interval.version
        or interval.start_ns != event.timestamp_ns
        or interval.start_ns != _serialized_time_ns(event.text("interval.started_at"))
        or (interval.end_ns is not None and interval.end_ns < interval.start_ns)
    ):
        raise ValueError("invalid lifecycle interval identity or bounds")
    fingerprint = hashlib.sha256(
        "\x1f".join(
            (
                interval.producer,
                interval.session,
                interval.opening_id,
                event.text("interval.started_at"),
                event.strings.get("interval.ended_at", ""),
                interval.project_id,
                interval.project_name,
            )
        ).encode()
    ).hexdigest()
    if fingerprint != event.text("interval.fingerprint"):
        raise ValueError("lifecycle fingerprint differs from immutable inputs")
    return interval


def _read_supersession(event: ImmutableEvent) -> _Supersession:
    edge = _Supersession(
        event.text("source.entity_id"),
        event.text("replacement.event_id"),
        (event.text("producer"), event.text("producer.surface"), event.text("session.id")),
        event.text("source.lifecycle_event"),
        event.text("replacement.lifecycle_event"),
    )
    kinds = {"session_start", "workspace_changed", "session_end"}
    if (
        edge.original == edge.replacement
        or _ACCEPTED_ID.fullmatch(edge.original) is None
        or _ACCEPTED_ID.fullmatch(edge.replacement) is None
        or event.text("entity.id") != f"lifecycle-supersession:{edge.original}"
        or event.count("entity.version") != 1
        or event.nanoseconds("supersession.version") != 1
        or edge.native_key[0] not in _INTERVAL_CAPABLE
        or edge.native_key[1] != edge.native_key[0]
        or edge.original_kind not in kinds
        or edge.replacement_kind not in kinds
    ):
        raise ValueError("invalid lifecycle supersession metadata")
    return edge


def _latest_intervals(histories: dict[str, dict[int, _Interval]]) -> dict[str, _Interval]:
    latest: dict[str, _Interval] = {}
    for opening, versions in histories.items():
        if min(versions) != 1 or len(versions) != max(versions):
            raise ValueError("lifecycle interval version continuity failed")
        headers = {
            (item.key, item.start_ns, item.project_id, item.project_name)
            for item in versions.values()
        }
        if len(headers) != 1:
            raise ValueError("lifecycle versions disagree on immutable authority")
        latest[opening] = versions[max(versions)]
    return latest


def _validate_supersession_links(
    edges: dict[str, _Supersession], intervals: dict[str, _Interval]
) -> None:
    if len({edge.replacement for edge in edges.values()}) != len(edges):
        raise ValueError("lifecycle replacement has multiple original identities")
    for edge in edges.values():
        following = edges.get(edge.replacement)
        if following is not None and (
            following.native_key != edge.native_key
            or following.original_kind != edge.replacement_kind
        ):
            raise ValueError("lifecycle supersession chain changes accepted identity")
        for identity, kind in (
            (edge.original, edge.original_kind),
            (edge.replacement, edge.replacement_kind),
        ):
            interval = intervals.get(identity)
            if interval is not None and (interval.key != edge.native_key or kind == "session_end"):
                raise ValueError("lifecycle supersession contradicts opening authority")


def _resolve_supersessions(
    edges: dict[str, _Supersession], intervals: dict[str, _Interval]
) -> None:
    _validate_supersession_links(edges, intervals)
    resolved: dict[str, tuple[str, str]] = {}
    for original in edges:
        trail: set[str] = set()
        current = original
        final_kind = edges[original].replacement_kind
        while current in edges and current not in resolved:
            if current in trail:
                raise ValueError("lifecycle supersession cycle")
            trail.add(current)
            final_kind = edges[current].replacement_kind
            current = edges[current].replacement
        if not trail:
            continue
        final = resolved.get(current, (current, final_kind))
        if final[1] != "session_end" and final[0] not in intervals:
            raise _IncompleteLifecycleError("replacement opening authority has not arrived")
        for identity in trail:
            resolved[identity] = final


def _lifecycle_events(
    rows: list[Mapping[str, Any]], manifest_cache: ManifestCache
) -> list[ImmutableEvent]:
    events = list(require_canonical_integrity(rows).values())
    for event in events:
        event.validate_identity(OPERATIONAL_SCOPE)
    for event in events:
        if (
            event.count("interval.payload_schema_version") != 2
            or event.count("event.sequence") != 1
        ):
            raise ValueError("unsupported lifecycle projection envelope")
        if event.text("event.name") == "introspection.session_context.population":
            _validate_population_envelope(event, manifest_cache)
    return events


def _parse_lifecycle(
    rows: list[Mapping[str, Any]], snapshots: list[dict[str, Any]]
) -> tuple[list[_Interval], set[str]]:
    histories: dict[str, dict[int, _Interval]] = defaultdict(dict)
    supersessions: dict[str, _Supersession] = {}
    manifest_cache = ManifestCache()
    events = _lifecycle_events(rows, manifest_cache)
    interval_ids: dict[str, tuple[int, str]] = {}
    supersession_ids: set[str] = set()
    for event in events:
        if event.text("event.name") == "introspection.session_context.interval.recorded":
            interval = _read_interval(event)
            if interval.version in histories[interval.opening_id]:
                raise ValueError("multiple event identities for lifecycle version")
            histories[interval.opening_id][interval.version] = interval
            prior = interval_ids.get(interval.opening_id)
            if prior is None or interval.version > prior[0]:
                interval_ids[interval.opening_id] = interval.version, event.text("event.id")
        elif event.text("event.name") == "introspection.session_context.superseded":
            edge = _read_supersession(event)
            if edge.original in supersessions:
                raise ValueError("multiple immutable supersessions for original event")
            supersessions[edge.original] = edge
            supersession_ids.add(event.text("event.id"))
        elif event.text("event.name") != "introspection.session_context.population":
            raise ValueError("unexpected lifecycle projection family")
    latest = _latest_intervals(histories)
    owners = _validate_lifecycle_population(
        events,
        {item[1] for item in interval_ids.values()},
        supersession_ids,
        snapshots,
        manifest_cache,
    )
    _resolve_supersessions(supersessions, latest)
    retained = [item for key, item in latest.items() if key not in supersessions]
    _reject_overlaps(retained)
    return retained, owners


def _reject_overlaps(retained: list[_Interval]) -> None:
    by_native: dict[tuple[str, str, str], list[_Interval]] = defaultdict(list)
    for interval in retained:
        by_native[interval.key].append(interval)
    for intervals in by_native.values():
        for before, after in pairwise(sorted(intervals, key=lambda item: item.start_ns)):
            if before.end_ns is None or before.end_ns > after.start_ns:
                raise ValueError("overlapping authoritative lifecycle intervals")


def _validate_population_envelope(event: ImmutableEvent, manifest_cache: ManifestCache) -> None:
    if (
        event.text("entity.id") != f"{event.text('scan.run_id')}:lifecycle-population"
        or event.count("entity.version") != 1
        or event.nanoseconds("lifecycle.observed_at_ns") != event.timestamp_ns
    ):
        raise ValueError("invalid lifecycle population envelope")
    event.text("scan.runtime_identity")
    read_event_ids(
        event,
        "lifecycle.interval_event_ids",
        "lifecycle.interval_count",
        manifest_cache=manifest_cache,
    )
    read_event_ids(
        event,
        "lifecycle.supersession_event_ids",
        "lifecycle.supersession_count",
        manifest_cache=manifest_cache,
    )


def _validate_lifecycle_population(
    events: list[ImmutableEvent],
    interval_ids: set[str],
    supersession_ids: set[str],
    snapshots: list[dict[str, Any]],
    manifest_cache: ManifestCache,
) -> set[str]:
    latest: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots:
        runtime = snapshot["runtime_identity"]
        previous = latest.get(runtime)
        if previous is None or (snapshot["completed_at_ns"], snapshot["event_id"]) > (
            previous["completed_at_ns"],
            previous["event_id"],
        ):
            latest[runtime] = snapshot
    if not latest:
        raise _IncompleteLifecycleError("no completed scan seals lifecycle authority")
    populations = {
        event.text("event.id"): event
        for event in events
        if event.text("event.name") == "introspection.session_context.population"
    }
    expected_intervals: set[str] = set()
    expected_supersessions: set[str] = set()
    for runtime, snapshot in latest.items():
        if snapshot["lifecycle.capture_state"] != "completed":
            raise _IncompleteLifecycleError("latest scan could not capture lifecycle authority")
        population = populations.get(snapshot["lifecycle.observation_event_id"])
        if population is None:
            raise _IncompleteLifecycleError("latest lifecycle population has not arrived")
        if (
            population.text("scan.run_id") != snapshot["scan_run_id"]
            or population.text("scan.runtime_identity") != runtime
            or population.timestamp_ns != snapshot["lifecycle.observed_at_ns"]
        ):
            raise ValueError("lifecycle population disagrees with completed scan")
        expected_intervals.update(
            read_event_ids(
                population,
                "lifecycle.interval_event_ids",
                "lifecycle.interval_count",
                manifest_cache=manifest_cache,
            )
        )
        expected_supersessions.update(
            read_event_ids(
                population,
                "lifecycle.supersession_event_ids",
                "lifecycle.supersession_count",
                manifest_cache=manifest_cache,
            )
        )
    if expected_intervals != interval_ids or expected_supersessions != supersession_ids:
        raise _IncompleteLifecycleError("complete latest lifecycle authority has not arrived")
    return {snapshot["scan_run_id"] for snapshot in latest.values()}


def _native_source_times(
    rows: list[SourceSessionRow],
) -> dict[tuple[str, str, str], set[int]]:
    sources: dict[tuple[str, str, str], set[int]] = defaultdict(set)
    for row in rows:
        at = row.source_timestamp_ns
        if at is None:
            raise ValueError("raw native source lacks exact timestamp")
        producer, surface = CANONICAL_SERVICE_PRODUCERS[row.service_name]
        sources[producer, surface, row.native_session_ids[0]].add(at)
    return sources


def _lifecycle_panels(
    rows: list[Mapping[str, Any]],
    raw_sources: list[SourceSessionRow],
    start_ns: int,
    end_ns: int,
    snapshots: list[dict[str, Any]],
) -> tuple[PipelinePanel, PipelinePanel, set[str]]:
    source_times = _native_source_times(raw_sources)
    sources = {key: min(times) for key, times in source_times.items()}
    cohort_source_keys: dict[tuple[str, str], set[tuple[str, str, str]]] = defaultdict(set)
    for key in sources:
        cohort_source_keys[key[:2]].add(key)
    intervals, owner_scan_ids = _parse_lifecycle(rows, snapshots)
    cohorts = set(cohort_source_keys) | {("codex-cli", "codex-cli")}
    interval_by_key: dict[tuple[str, str, str], list[_Interval]] = defaultdict(list)
    lifecycle_keys: set[tuple[str, str, str]] = set()
    cohort_lifecycle_keys: dict[tuple[str, str], set[tuple[str, str, str]]] = defaultdict(set)
    for interval in intervals:
        interval_by_key[interval.key].append(interval)
        if start_ns < interval.start_ns <= end_ns:
            lifecycle_keys.add(interval.key)
            cohort_lifecycle_keys[interval.key[:2]].add(interval.key)
            cohorts.add(interval.key[:2])
    matched = {
        key: next(
            (
                interval
                for interval in sorted(
                    interval_by_key[key], key=lambda value: (value.start_ns, value.opening_id)
                )
                if interval.contains(at)
            ),
            None,
        )
        for key, at in sources.items()
    }
    cohort_rows: list[list[str | int | float | None]] = []
    metrics: list[PipelineMetric] = []
    delays_by_cohort: dict[tuple[str, str], list[float]] = defaultdict(list)
    skew_by_cohort: dict[tuple[str, str], int] = defaultdict(int)
    for producer, surface in sorted(cohorts):
        cohort = producer, surface
        source_keys = cohort_source_keys[cohort]
        if producer not in _INTERVAL_CAPABLE:
            cohort_rows.append(
                [
                    producer,
                    surface,
                    "Not applicable",
                    len(source_keys),
                    None,
                    None,
                    None,
                    None,
                    None,
                ]
            )
            continue
        lifecycle = cohort_lifecycle_keys[cohort]
        source_matched = sum(matched[key] is not None for key in source_keys)
        lifecycle_matched = sum(
            any(
                interval.contains(at)
                for interval in interval_by_key[key]
                for at in source_times.get(key, ())
            )
            for key in lifecycle
        )
        cohort_rows.append(
            [
                producer,
                surface,
                "interval",
                len(source_keys),
                source_matched,
                len(source_keys) - source_matched,
                len(lifecycle),
                lifecycle_matched,
                len(lifecycle) - lifecycle_matched,
            ]
        )
        metrics.extend(
            [
                _metric(
                    f"{producer}/{surface} source sessions",
                    "sessions",
                    _MetricContents(len(source_keys)),
                ),
                _metric(
                    f"{producer}/{surface} source-to-lifecycle coverage",
                    "percent",
                    _MetricContents(
                        100 * source_matched / len(source_keys) if source_keys else None,
                        source_matched,
                        len(source_keys),
                    ),
                ),
                _metric(
                    f"{producer}/{surface} lifecycle-to-source coverage",
                    "percent",
                    _MetricContents(
                        100 * lifecycle_matched / len(lifecycle) if lifecycle else None,
                        lifecycle_matched,
                        len(lifecycle),
                    ),
                ),
            ]
        )
    for key, matched_interval in matched.items():
        if matched_interval is None:
            continue
        delay = (sources[key] - matched_interval.start_ns) / 1_000_000
        cohort = key[:2]
        if delay < 0:
            skew_by_cohort[cohort] += 1
        else:
            delays_by_cohort[cohort].append(delay)
    delay_rows: list[list[Scalar]] = [
        [
            producer,
            surface,
            len(delays_by_cohort[producer, surface]) + skew_by_cohort[producer, surface],
            _percentile(delays_by_cohort[producer, surface], 50),
            _percentile(delays_by_cohort[producer, surface], 95),
            skew_by_cohort[producer, surface],
        ]
        if producer in _INTERVAL_CAPABLE
        else [producer, surface, None, None, None, None]
        for producer, surface in sorted(cohorts)
    ]
    p5_state = (
        "Data"
        if lifecycle_keys or any(key[0] in _INTERVAL_CAPABLE for key in sources)
        else "Not applicable"
        if sources
        else "No data"
    )
    capability_reason = "codex-cli supplies point context, not lifecycle interval authority"
    p5 = _panel(
        p5_state,
        "Per producer/surface directional namespaced native sessions",
        "first raw source for forward coverage; any contained raw source for reverse coverage",
        _PanelContents(
            metrics,
            [
                "Producer",
                "Surface",
                "Capability",
                "Source sessions",
                "Source matched",
                "Source without lifecycle",
                "Lifecycle sessions",
                "Lifecycle matched",
                "Lifecycle without source",
            ],
            cohort_rows[:50],
            [capability_reason],
        ),
    )
    p6 = _panel(
        "Data"
        if any(row[2] for row in delay_rows)
        else ("Not applicable" if p5_state == "Not applicable" else "No data"),
        "Matched source sessions using first raw source event in range",
        "first in-range raw source timestamp",
        _PanelContents(
            [],
            [
                "Producer",
                "Surface",
                "Matched sessions",
                "Delay p50 ms",
                "Delay p95 ms",
                "Negative clock-skew sessions",
            ],
            delay_rows[:50],
            [capability_reason],
        ),
    )
    return p5, p6, owner_scan_ids


def _failed_attribution(state: str, reason: str) -> PipelinePanel:
    return _panel(
        state, "Outcome unknown", "remote canonical evidence", _PanelContents([], [], [], [reason])
    )


def _query_activity_panels(
    client: EventQueryClient, start_ns: int, end_ns: int
) -> tuple[PipelinePanel, PipelinePanel, PipelinePanel, PipelinePanel]:
    try:
        rows = list(client.query(_ACTIVITY_SQL, {"start": start_ns, "end": end_ns}))
    except Exception:
        failure = _failed_attribution("Query/system error", "Remote activity query failed")
    else:
        try:
            return _activity_panels(rows, start_ns, end_ns)
        except (ValueError, KeyError, TypeError, OverflowError):
            failure = _failed_attribution(
                "Integrity failure", "Immutable canonical activity history is invalid"
            )
    return failure, failure.copy(), failure.copy(), failure.copy()


def _query_lifecycle_panels(
    client: EventQueryClient,
    window: PipelineWindow,
    measurement_start_ns: int,
    snapshot_cache: SnapshotCache | None,
) -> tuple[PipelinePanel, PipelinePanel, set[str]]:
    start_ns, end_ns, evaluated_at_ns = window.start_ns, window.end_ns, window.evaluated_at_ns
    try:
        rows = list(client.query(_LIFECYCLE_SQL, {"evaluated_at": evaluated_at_ns}))
    except Exception:
        failure = _failed_attribution("Query/system error", "Remote lifecycle query failed")
        return failure, failure.copy(), set()
    try:
        raw = query_raw_native_sources(client, start_ns=start_ns, end_ns=end_ns)
        snapshots = query_pipeline_snapshots(
            client,
            start_ns=measurement_start_ns,
            end_ns=evaluated_at_ns,
            snapshot_cache=snapshot_cache,
        )
        return _lifecycle_panels(rows, raw, start_ns, end_ns, snapshots)
    except SourceError:
        failure = _failed_attribution("Query/system error", "Remote native source query failed")
    except RuntimeError:
        failure = _failed_attribution("Query/system error", "Remote scan authority query failed")
    except _IncompleteLifecycleError:
        failure = _failed_attribution(
            "Unavailable", "Required complete lifecycle authority projection has not arrived"
        )
    except (ValueError, KeyError, TypeError, OverflowError):
        failure = _failed_attribution(
            "Integrity failure", "Immutable lifecycle authority or raw native source is invalid"
        )
    return failure, failure.copy(), set()


def query_attribution(
    client: EventQueryClient,
    *,
    window: PipelineWindow,
    measurement_start_ns: int | None = None,
    snapshot_cache: SnapshotCache | None = None,
) -> tuple[dict[str, PipelinePanel], set[str]]:
    start_ns, end_ns, evaluated_at_ns = window.start_ns, window.end_ns, window.evaluated_at_ns
    if (
        any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (start_ns, end_ns, evaluated_at_ns)
        )
        or not 0 <= start_ns < end_ns <= evaluated_at_ns < 2**64
    ):
        raise ValueError("timestamps must be ordered integer nanoseconds")
    if measurement_start_ns is not None and (
        isinstance(measurement_start_ns, bool)
        or not isinstance(measurement_start_ns, int)
        or not 0 < measurement_start_ns < 2**64
    ):
        raise ValueError("measurement_start_ns must be a positive integer nanosecond timestamp")
    measurement_start = measurement_start_ns or 0
    if end_ns <= measurement_start:
        unavailable = _failed_attribution(
            "Unavailable", "requested range precedes the fresh measurement start"
        )
        return {
            "p5-correlation": unavailable,
            "p6-delay": unavailable.copy(),
            "p7-coverage": unavailable.copy(),
            "p8-diagnostics": unavailable.copy(),
            "p9-transitions": unavailable.copy(),
            "p9-evidence": unavailable.copy(),
        }, set()
    contributing_start = max(start_ns, measurement_start)
    p7, p8, p9, p9_evidence = _query_activity_panels(client, contributing_start, end_ns)
    p5, p6, owner_scan_ids = _query_lifecycle_panels(
        client,
        PipelineWindow(contributing_start, end_ns, evaluated_at_ns),
        measurement_start,
        snapshot_cache,
    )
    panels = {
        "p5-correlation": p5,
        "p6-delay": p6,
        "p7-coverage": p7,
        "p8-diagnostics": p8,
        "p9-transitions": p9,
        "p9-evidence": p9_evidence,
    }
    return panels, owner_scan_ids

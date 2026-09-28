"""Fail-closed integrity checks for immutable Pipeline telemetry."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from agent_introspection.pipeline_events import (
    ImmutableEvent,
    ImmutableNumberError,
    decode_immutable_event,
    parse_timestamp,
)
from agent_introspection.telemetry import (
    CANONICAL_ACTIVITY_EVENT_NAME,
    CANONICAL_ACTIVITY_PAYLOAD_SCHEMA_VERSION,
    OPERATIONAL_SCOPE,
    CanonicalActivityVersionEvent,
    DerivedEvent,
    EventQueryClient,
)


class IntegrityInvariant(StrEnum):
    EMPTY_IDENTITY = "empty_identity"
    SAME_ID_DIVERGENCE = "same_id_divergence"
    MULTIPLE_EVENT_IDS = "multiple_event_ids"
    DETERMINISTIC_ID_MISMATCH = "deterministic_id_mismatch"
    VERSION_GAP = "version_gap"
    CONFLICTING_PROJECT_NAME = "conflicting_project_name"
    CONFLICTING_NATIVE_SESSION_IDENTITY = "conflicting_native_session_identity"
    NEGATIVE_IMPOSSIBLE_COUNT = "negative_impossible_count"


@dataclass(frozen=True, slots=True)
class IntegrityIncident:
    invariant: IntegrityInvariant
    subject: str
    origin: str
    producer: str = ""
    surface: str = ""
    source_time_ns: int | None = None


INTEGRITY_INCIDENT_EVENT = "introspection.pipeline.integrity_incident"
INTEGRITY_AUDIT_EVENT = "introspection.pipeline.integrity_audit"
_SOURCE_EVENT = "introspection.source_session.recorded"
_INTERVAL_EVENT = "introspection.session_context.interval.recorded"
_HISTORY_EVENTS = frozenset({CANONICAL_ACTIVITY_EVENT_NAME, _SOURCE_EVENT, _INTERVAL_EVENT})
_EVENT_NAMES = _HISTORY_EVENTS | {
    "introspection.session_context.superseded",
    "introspection.session_context.population",
    "introspection.pipeline.snapshot",
    "introspection.pipeline.source_lag",
    "introspection.pipeline.ledger_maintenance",
    "introspection.pipeline.delivery.final_drain",
    "introspection.pipeline.delivery.event",
    "introspection.pipeline.delivery.attempt",
}
PIPELINE_AUTHORITY_EVENTS = _EVENT_NAMES | {
    INTEGRITY_INCIDENT_EVENT,
    INTEGRITY_AUDIT_EVENT,
    "introspection.database.backup.observed",
}
_REMOTE_INTEGRITY_SQL = """
SELECT timestamp, body, attributes_string AS strings,
       mapApply((key, value) -> (key, toString(reinterpretAsUInt64(value))),
                attributes_number) AS number_bits,
       attributes_bool AS booleans
FROM agent_introspection.events
WHERE resource.`service.name`::String = 'agent-introspection'
  AND timestamp <= {end:UInt64}
  AND (
    attributes_string['event.name'] IN (
      'introspection.activity.version.recorded',
      'introspection.source_session.recorded',
      'introspection.session_context.interval.recorded',
      'introspection.session_context.superseded',
      'introspection.session_context.population',
      'introspection.pipeline.delivery.final_drain',
      'introspection.pipeline.delivery.event',
      'introspection.pipeline.delivery.attempt'
    )
    OR (attributes_number['pipeline.payload_schema_version'] = toFloat64(2)
        AND attributes_string['event.name'] NOT IN (
          'introspection.pipeline.integrity_incident',
          'introspection.pipeline.integrity_audit'
        ))
  )
  AND NOT (attributes_string['event.name'] = 'introspection.activity.version.recorded'
           AND attributes_number['activity.payload_schema_version'] = toFloat64(1))
ORDER BY timestamp, attributes_string['event.id']
""".strip()
_CANONICAL_TEXT = (
    "activity.id",
    "event.id",
    "event.name",
    "activity.producer",
    "activity.producer_surface",
    "activity.correlation_id",
    "activity.detector.id",
    "activity.attribution.state",
    "activity.attribution.method",
    "agent.project.id",
    "agent.project.name",
)
_CANONICAL_COUNTS = (
    "activity.version",
    "activity.payload_schema_version",
    "activity.detector.version",
    "activity.normalization.version",
)
_COUNT_FIELDS = frozenset(
    {
        "rows.processed",
        "outbox.pending_after_drain",
        "outbox.failed_during_drain",
        "database.bytes",
        "database.wal_bytes",
        "backup.bytes",
    }
)


def _subject(*parts: object) -> str:
    serialized = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()


def _text(values: Mapping[str, Any], key: str) -> str:
    value = values.get(key)
    return value if isinstance(value, str) else ""


def _count(value: object, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid immutable count")
    # Bound integers before float conversion: arbitrarily large JSON integers are invalid,
    # not an OverflowError that can escape the fail-closed boundary.
    if value < int(positive) or value > 2**53 - 1 or not math.isfinite(value):
        raise ValueError("invalid immutable count")
    if int(value) != value:
        raise ValueError("invalid immutable count")
    return int(value)


def _read_row(row: Mapping[str, Any]) -> ImmutableEvent:
    return decode_immutable_event(row)


def _incident(
    invariant: IntegrityInvariant, event: ImmutableEvent, subject: object
) -> IntegrityIncident:
    strings = event.strings
    producer = (
        _text(strings, "activity.producer")
        or _text(strings, "source.producer")
        or _text(strings, "producer")
    )
    surface = (
        _text(strings, "activity.producer_surface")
        or _text(strings, "source.producer_surface")
        or _text(strings, "producer.surface")
    )
    if producer not in {"omp", "codex-cli", "codex-app-server"}:
        producer = ""
    if surface != producer:
        surface = ""
    name = _text(strings, "event.name")
    return IntegrityIncident(
        invariant,
        _subject(subject),
        name if name in _EVENT_NAMES else "unknown",
        producer,
        surface,
        event.timestamp_ns,
    )


@dataclass(slots=True)
class _Population:
    incidents: dict[tuple[IntegrityInvariant, str], IntegrityIncident] = field(default_factory=dict)
    physical: dict[str, ImmutableEvent] = field(default_factory=dict)
    logical: dict[tuple[str, str, int, int, str], set[str]] = field(
        default_factory=lambda: defaultdict(set)
    )
    contexts: dict[tuple[str, str], list[ImmutableEvent]] = field(
        default_factory=lambda: defaultdict(list)
    )
    versions: dict[tuple[str, str], set[int]] = field(default_factory=lambda: defaultdict(set))
    project_names: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    project_contexts: dict[str, list[ImmutableEvent]] = field(
        default_factory=lambda: defaultdict(list)
    )
    native_tuples: dict[tuple[str, str], set[tuple[str, str, str]]] = field(
        default_factory=lambda: defaultdict(set)
    )
    source_headers: dict[tuple[str, str], set[tuple[int, str, int, int]]] = field(
        default_factory=lambda: defaultdict(set)
    )

    def add(self, invariant: IntegrityInvariant, event: ImmutableEvent, subject: object) -> None:
        self.record(_incident(invariant, event, subject))

    def record(self, incident: IntegrityIncident) -> None:
        key = incident.invariant, incident.subject
        prior = self.incidents.get(key)
        if prior is not None:
            incident = replace(
                incident,
                origin=incident.origin if incident.origin == prior.origin else "unknown",
                producer=incident.producer if incident.producer == prior.producer else "",
                surface=incident.surface if incident.surface == prior.surface else "",
                source_time_ns=(
                    incident.source_time_ns
                    if incident.source_time_ns == prior.source_time_ns
                    else None
                ),
            )
        self.incidents[key] = incident

    def project(self, event: ImmutableEvent) -> None:
        project = event.text("agent.project.id")
        self.project_names[project].add(event.text("agent.project.name"))
        self.project_contexts[project].append(event)


def _check_numbers(population: _Population, event: ImmutableEvent) -> None:
    event_id = _text(event.strings, "event.id")
    for key in event.strings:
        if key.endswith("count") or key in _COUNT_FIELDS:
            population.add(IntegrityInvariant.NEGATIVE_IMPOSSIBLE_COUNT, event, (event_id, key))
    for key in event.booleans:
        if key.endswith("count") or key in _COUNT_FIELDS:
            population.add(IntegrityInvariant.NEGATIVE_IMPOSSIBLE_COUNT, event, (event_id, key))
    for key, value in event.numbers.items():
        try:
            event.number(key)
            if key.endswith("count") or key in _COUNT_FIELDS:
                _count(value)
            if key == "scan.duration_ms" and event.number(key) < 0:
                raise ValueError("negative duration")
        except ValueError:
            population.add(IntegrityInvariant.NEGATIVE_IMPOSSIBLE_COUNT, event, (event_id, key))


def _check_physical(population: _Population, event: ImmutableEvent) -> None:
    event_id = event.text("event.id")
    prior = population.physical.get(event_id)
    if prior is not None and not prior.same_physical(event):
        population.add(IntegrityInvariant.SAME_ID_DIVERGENCE, event, event_id)
        population.add(IntegrityInvariant.SAME_ID_DIVERGENCE, prior, event_id)
    else:
        population.physical[event_id] = event


def _canonical_state(event: ImmutableEvent) -> None:
    state = event.text("activity.attribution.state")
    project = event.text("agent.project.id")
    reason = _text(event.strings, "activity.attribution.reason_code")
    if state not in {"resolved", "unresolved"}:
        raise ValueError("invalid canonical attribution state")
    if (state == "unresolved") != (project == "unresolved"):
        raise ValueError("contradictory canonical attribution state")
    if (state == "unresolved") != bool(reason):
        raise ValueError("contradictory canonical attribution reason")
    producer = event.text("activity.producer")
    if producer not in {"omp", "codex-cli", "codex-app-server"} or (
        event.text("activity.producer_surface") != producer
    ):
        raise ValueError("unsupported canonical producer or surface")
    evidence = _text(event.strings, "activity.attribution.evidence_id")
    identity = _text(event.strings, "activity.attribution.project_identity_id")
    if state == "resolved":
        if not evidence or identity != project:
            raise ValueError("resolved attribution lacks canonical project evidence")
    elif evidence or identity or event.text("agent.project.name") != "unresolved":
        raise ValueError("unresolved attribution declares resolved evidence")


def _check_canonical(population: _Population, event: ImmutableEvent) -> None:
    schema = _count(event.numbers.get("activity.payload_schema_version"), positive=True)
    if schema == 1:
        return
    if (
        schema != CANONICAL_ACTIVITY_PAYLOAD_SCHEMA_VERSION
        or event.text("event.scope") != "canonical-activity"
    ):
        raise ValueError("invalid canonical schema")
    for key in _CANONICAL_TEXT:
        event.text(key)
    for key in _CANONICAL_COUNTS:
        _count(event.numbers.get(key), positive=True)
    _canonical_state(event)
    activity = event.text("activity.id")
    version = event.count("activity.version")
    name = event.text("event.name")
    entity = name, activity
    population.contexts[entity].append(event)
    population.versions[entity].add(version)
    population.logical["canonical-activity", activity, version, 0, name].add(event.text("event.id"))
    population.project(event)
    population.native_tuples[entity].add(
        (
            event.text("activity.producer"),
            event.text("activity.producer_surface"),
            event.text("activity.correlation_id"),
        )
    )
    population.source_headers[entity].add(
        (
            event.timestamp_ns,
            event.text("activity.detector.id"),
            event.count("activity.detector.version"),
            event.count("activity.normalization.version"),
        )
    )
    expected = CanonicalActivityVersionEvent(activity, version, event.timestamp_ns, {}).event_id
    if event.text("event.id") != expected:
        population.add(IntegrityInvariant.DETERMINISTIC_ID_MISMATCH, event, (activity, version))


def _check_derived(population: _Population, event: ImmutableEvent) -> None:
    scope, entity, name = (
        event.text("event.scope"),
        event.text("entity.id"),
        event.text("event.name"),
    )
    if name not in _EVENT_NAMES:
        raise ValueError("unsupported canonical event family")
    version = _count(event.numbers.get("entity.version"), positive=True)
    sequence = _count(event.numbers.get("event.sequence"))
    event_id = event.text("event.id")
    logical = scope, entity, version, sequence, name
    population.logical[logical].add(event_id)
    identity = name, entity
    population.contexts[identity].append(event)
    expected = DerivedEvent(scope, entity, version, sequence, name, {}, event.timestamp_ns).event_id
    if event_id != expected:
        population.add(IntegrityInvariant.DETERMINISTIC_ID_MISMATCH, event, logical)
    if name in _HISTORY_EVENTS:
        population.versions[identity].add(version)
    if name == _SOURCE_EVENT:
        native = (
            _text(event.strings, "source.producer"),
            _text(event.strings, "source.producer_surface"),
            _text(event.strings, "source.session.id"),
        )
        if all(native):
            population.native_tuples[identity].add(native)
    elif name == _INTERVAL_EVENT:
        population.native_tuples[identity].add(
            (
                event.text("producer"),
                event.text("producer.surface"),
                event.text("session.id"),
            )
        )
        population.project(event)


def _inspect_row(population: _Population, row: Mapping[str, Any]) -> None:
    try:
        event = _read_row(row)
    except ImmutableNumberError:
        population.record(
            IntegrityIncident(
                IntegrityInvariant.NEGATIVE_IMPOSSIBLE_COUNT, _subject(row), "unknown"
            )
        )
        return
    except (TypeError, ValueError):
        population.record(
            IntegrityIncident(IntegrityInvariant.EMPTY_IDENTITY, _subject(row), "unknown")
        )
        return
    _check_numbers(population, event)
    try:
        name = event.text("event.name")
        _check_physical(population, event)
        if name == CANONICAL_ACTIVITY_EVENT_NAME:
            _check_canonical(population, event)
        else:
            _check_derived(population, event)
    except (KeyError, TypeError, ValueError):
        population.add(IntegrityInvariant.EMPTY_IDENTITY, event, _subject(row))


def _add_context_incidents(
    population: _Population,
    invariant: IntegrityInvariant,
    events: list[ImmutableEvent],
    subject: object,
) -> None:
    for event in events:
        population.add(invariant, event, subject)


def _check_histories(population: _Population) -> None:
    for logical, ids in population.logical.items():
        if len(ids) > 1:
            _add_context_incidents(
                population,
                IntegrityInvariant.MULTIPLE_EVENT_IDS,
                population.contexts[logical[-1], logical[1]],
                logical,
            )
    for identity, versions in population.versions.items():
        if min(versions) != 1 or max(versions) != len(versions):
            _add_context_incidents(
                population,
                IntegrityInvariant.VERSION_GAP,
                population.contexts[identity],
                identity,
            )


def _finalize(population: _Population) -> list[IntegrityIncident]:
    _check_histories(population)
    for project, names in population.project_names.items():
        if len(names) > 1:
            _add_context_incidents(
                population,
                IntegrityInvariant.CONFLICTING_PROJECT_NAME,
                population.project_contexts[project],
                project,
            )
    for identity, tuples in population.native_tuples.items():
        if len(tuples) > 1:
            _add_context_incidents(
                population,
                IntegrityInvariant.CONFLICTING_NATIVE_SESSION_IDENTITY,
                population.contexts[identity],
                identity,
            )
    for identity, headers in population.source_headers.items():
        if len(headers) > 1:
            _add_context_incidents(
                population,
                IntegrityInvariant.SAME_ID_DIVERGENCE,
                population.contexts[identity],
                (identity, "source-header"),
            )
    return sorted(
        population.incidents.values(),
        key=lambda item: (
            item.invariant.value,
            item.subject,
            item.origin,
            item.source_time_ns or 0,
        ),
    )


def _inspect_population(rows: Sequence[Mapping[str, Any]]) -> _Population:
    population = _Population()
    for row in rows:
        _inspect_row(population, row)
    return population


def inspect_canonical_events(rows: Sequence[Mapping[str, Any]]) -> list[IntegrityIncident]:
    """Inspect complete raw-map histories before deduplication, caps or latest selection."""
    return _finalize(_inspect_population(rows))


def require_canonical_integrity(
    rows: Sequence[Mapping[str, Any]],
) -> Mapping[str, ImmutableEvent]:
    """Fail closed without exposing native identifiers or remote payloads.

    Return validated physical envelopes for request-local reuse.
    """
    population = _inspect_population(rows)
    if _finalize(population):
        raise ValueError("canonical telemetry integrity validation failed")
    return population.physical


def capture_remote_integrity(
    client: EventQueryClient, *, scan_run_id: str, runtime_identity: str, observed_at_ns: int
) -> list[DerivedEvent]:
    """Seal an actual remote audit and its exact immutable incident population."""
    if not scan_run_id or not runtime_identity:
        raise ValueError("invalid scan integrity identity")
    observed_at_ns = parse_timestamp(observed_at_ns)
    # Preserve query failures and scan deadlines. Neither is a completed zero-incident audit.
    rows = list(client.query(_REMOTE_INTEGRITY_SQL, {"end": observed_at_ns}))
    incidents = inspect_canonical_events(rows)
    events = []
    for item in incidents:
        attributes: dict[str, str | int | float | bool] = {
            "pipeline.payload_schema_version": 2,
            "incident.class": "integrity_failure",
            "incident.invariant": item.invariant.value,
            "incident.origin": item.origin,
            "incident.producer": item.producer,
            "incident.surface": item.surface,
            "incident.source_time_ns": str(observed_at_ns),
            "incident.observed_at_ns": str(observed_at_ns),
            "scan.run_id": scan_run_id,
            "scan.runtime_identity": runtime_identity,
        }
        if item.source_time_ns is not None:
            attributes["incident.event_source_time_ns"] = str(item.source_time_ns)
        events.append(
            DerivedEvent(
                OPERATIONAL_SCOPE,
                _subject(scan_run_id, item.invariant.value, item.subject),
                1,
                1,
                INTEGRITY_INCIDENT_EVENT,
                attributes,
                observed_at_ns,
            )
        )
    events.append(
        DerivedEvent(
            OPERATIONAL_SCOPE,
            f"{scan_run_id}:integrity-audit",
            1,
            1,
            INTEGRITY_AUDIT_EVENT,
            {
                "pipeline.payload_schema_version": 2,
                "scan.run_id": scan_run_id,
                "scan.runtime_identity": runtime_identity,
                "audit.state": "completed",
                "audit.upper_bound_ns": str(observed_at_ns),
                "audit.physical_count": len(rows),
                "audit.incident_count": len(events),
                "audit.incident_event_ids": json.dumps(
                    sorted(event.event_id for event in events), separators=(",", ":")
                ),
            },
            observed_at_ns,
        )
    )
    return events

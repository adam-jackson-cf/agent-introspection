"""Read-only SQLite extractor for the E-Attribution-4 lifecycle-delay proof."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime

from experiments.dashboard_prototype.attribution_common import AttributionExperimentId
from experiments.dashboard_prototype.attribution_lifecycle_delay import (
    AcceptedLifecycleInterval,
    AuthoritativeSourceEvent,
    LifecycleDelayCohort,
    LifecycleDelayReduction,
    build_lifecycle_delay_proof,
    reduce_lifecycle_delay,
)
from experiments.dashboard_prototype.attribution_live_common import (
    AttributionCalculationPrimitive,
    AttributionLiveEvidence,
    LiveProofRequest,
)
from experiments.dashboard_prototype.contracts import EvidenceProvenance

_SUPPORTED_SERVICE_PRODUCERS = {
    "omp": ("omp", "omp"),
    "oh-my-pi": ("omp", "omp"),
    "codex-cli": ("codex-cli", "codex-cli"),
    "codex_exec": ("codex-cli", "codex-cli"),
    "codex_cli_rs": ("codex-cli", "codex-cli"),
    "codex-app-server": ("codex-app-server", "codex-app-server"),
}


_REMOTE_QUERY_ID = "attribution-lifecycle-delay-v1"
_REQUIRED_RECORD_COLUMNS = {
    "source_kind",
    "service_name",
    "source_id",
    "source_timestamp",
    "context_evidence_id",
    "session_ids_json",
    "thread_ids_json",
}
_REQUIRED_EVENT_COLUMNS = {"event_id", "producer", "session_id", "occurred_at"}
_REQUIRED_INTERVAL_COLUMNS = {"event_id", "producer", "session_id", "started_at", "ended_at"}


def extract(connection: sqlite3.Connection, request: LiveProofRequest) -> AttributionLiveEvidence:
    """Extract raw source rows only after exact lifecycle identity linkage."""
    missing = _missing_schema(connection)
    if missing:
        return _blocked(request, tuple(f"sqlite.schema.{name}" for name in missing))
    sources, source_blocked, source_conflict = _sources(connection, request)
    intervals, interval_blocked, interval_conflict = _intervals(connection)
    if source_conflict or interval_conflict:
        return _failed(request)
    cohorts = tuple(sorted({row.cohort for row in sources + intervals}))
    if not cohorts:
        return _blocked(request, ("authoritative-source-session",))
    reduction = reduce_lifecycle_delay(sources, intervals, cohorts, request.start, request.end)
    blocked = set(source_blocked) | set(interval_blocked) | set(reduction.blocked_boundaries)
    if not reduction.selections:
        blocked.add("authoritative-source-session")
    if blocked:
        reduction = LifecycleDelayReduction(
            reduction.selections,
            reduction.cohorts,
            tuple(sorted(blocked)),
            reduction.contradictory,
        )
    proof = build_lifecycle_delay_proof(
        request.run_id, EvidenceProvenance.FRESH_REAL, request.source_boundary, reduction
    )
    primitives = tuple(
        AttributionCalculationPrimitive(
            experiment_id=AttributionExperimentId.LIFECYCLE_DELAY,
            source_time=row.source_time,
            ordinal=ordinal,
            dimensions={
                "cohort": _hash(("cohort", row.cohort.boundary)),
                "evidence": _hash(("source", row.source_event_id)),
                "matched": row.matched,
            },
            measures={
                "lifecycle_delay_seconds": row.delay_seconds,
                "negative_skew": int(row.negative_skew),
            },
        )
        for ordinal, row in enumerate(reduction.selections, start=1)
        if row.matched and row.delay_seconds is not None
    )
    oracle = {
        _hash(("cohort", metric.cohort.boundary)): {
            "selected_sessions": metric.selected_sessions,
            "matched_sessions": metric.matched_sessions,
            "negative_skew_sessions": metric.negative_skew_sessions,
            "n": metric.n,
            "p50_lifecycle_delay_seconds": metric.p50_delay_seconds,
            "p95_lifecycle_delay_seconds": metric.p95_delay_seconds,
        }
        for metric in reduction.cohorts
        if metric.matched_sessions
    }
    return AttributionLiveEvidence(
        proof,
        primitives,
        _REMOTE_QUERY_ID if primitives else None,
        oracle,
    )


def _sources(
    connection: sqlite3.Connection, request: LiveProofRequest
) -> tuple[list[AuthoritativeSourceEvent], set[str], bool]:
    rows = tuple(
        connection.execute(
            """
            SELECT source_kind, service_name, source_id, source_timestamp, context_evidence_id,
                   session_ids_json, thread_ids_json
            FROM source_session_records
            WHERE source_timestamp > ? AND source_timestamp <= ?
            ORDER BY source_timestamp, source_id
            """,
            (request.start.isoformat(), request.end.isoformat()),
        )
    )
    events = {
        (str(event_id), str(producer), str(session_id))
        for event_id, producer, session_id in connection.execute(
            "SELECT event_id, producer, session_id FROM session_context_events"
        )
    }
    blocked: Counter[tuple[str, str, str]] = Counter()
    result: list[AuthoritativeSourceEvent] = []
    seen: dict[str, AuthoritativeSourceEvent] = {}
    for kind, service, source_id, timestamp, context_id, sessions_json, threads_json in rows:
        mapped = _SUPPORTED_SERVICE_PRODUCERS.get(str(service))
        if mapped is None:
            continue
        producer, surface = mapped
        if context_id is None or not str(context_id):
            blocked[("missing-context-evidence", producer, surface)] += 1
            continue
        native_ids = _native_ids(producer, sessions_json, threads_json)
        if len(native_ids) != 1:
            blocked[("native-identity-cardinality", producer, surface)] += 1
            continue
        native_id = native_ids[0]
        context_key = (str(context_id), producer, native_id)
        if context_key not in events:
            blocked[("context-event-linkage", producer, surface)] += 1
            continue
        try:
            source_time = _instant(timestamp)
        except (TypeError, ValueError):
            blocked[("source-timestamp", producer, surface)] += 1
            continue
        event = AuthoritativeSourceEvent(
            _hash(("source", kind, service, source_id)),
            LifecycleDelayCohort(producer, surface),
            native_id,
            source_time,
        )
        existing = seen.setdefault(event.event_id, event)
        if existing != event:
            return result, _blocked_summaries(blocked), True
        result.append(event)
    return result, _blocked_summaries(blocked), False


def _intervals(
    connection: sqlite3.Connection,
) -> tuple[list[AcceptedLifecycleInterval], set[str], bool]:
    rows = tuple(
        connection.execute(
            """
            SELECT event_id, producer, session_id, started_at, ended_at
            FROM session_context_intervals
            ORDER BY producer, session_id, started_at, event_id
            """
        )
    )
    blocked: Counter[tuple[str, str, str]] = Counter()
    intervals: list[AcceptedLifecycleInterval] = []
    seen: dict[str, AcceptedLifecycleInterval] = {}
    for event_id, producer, session_id, started_at, ended_at in rows:
        mapped = _SUPPORTED_SERVICE_PRODUCERS.get(str(producer))
        if mapped is None or mapped[0] != str(producer):
            continue
        try:
            interval = AcceptedLifecycleInterval(
                _hash(("interval", event_id)),
                LifecycleDelayCohort(*mapped),
                str(session_id),
                _instant(started_at),
                _instant(ended_at) if ended_at is not None else None,
            )
        except (TypeError, ValueError):
            blocked[("lifecycle-timestamp", *mapped)] += 1
            continue
        existing = seen.setdefault(interval.event_id, interval)
        if existing != interval:
            return intervals, _blocked_summaries(blocked), True
        intervals.append(interval)
    return intervals, _blocked_summaries(blocked), False


def _blocked_summaries(blocked: Counter[tuple[str, str, str]]) -> set[str]:
    return {
        f"{reason}:{producer}:{surface}:count={count}"
        for (reason, producer, surface), count in blocked.items()
    }


def _native_ids(producer: str, sessions_json: object, threads_json: object) -> tuple[str, ...]:
    raw = sessions_json if producer == "omp" else threads_json
    try:
        values = json.loads(str(raw))
    except (TypeError, json.JSONDecodeError):
        return ()
    if not isinstance(values, list) or any(
        not isinstance(value, str) or not value for value in values
    ):
        return ()
    return tuple(sorted(set(values)))


def _missing_schema(connection: sqlite3.Connection) -> tuple[str, ...]:
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    missing = {
        "source_session_records",
        "session_context_events",
        "session_context_intervals",
    } - tables
    if missing:
        return tuple(sorted(missing))
    columns = {
        "source_session_records": _columns(connection, "source_session_records"),
        "session_context_events": _columns(connection, "session_context_events"),
        "session_context_intervals": _columns(connection, "session_context_intervals"),
    }
    return tuple(
        sorted(
            name
            for name, required in (
                ("source_session_records", _REQUIRED_RECORD_COLUMNS),
                ("session_context_events", _REQUIRED_EVENT_COLUMNS),
                ("session_context_intervals", _REQUIRED_INTERVAL_COLUMNS),
            )
            if not required <= columns[name]
        )
    )


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _blocked(request: LiveProofRequest, boundaries: tuple[str, ...]) -> AttributionLiveEvidence:
    proof = build_lifecycle_delay_proof(
        request.run_id,
        EvidenceProvenance.FRESH_REAL,
        request.source_boundary,
        LifecycleDelayReduction((), (), boundaries),
    )
    return AttributionLiveEvidence(proof, (), None)


def _failed(request: LiveProofRequest) -> AttributionLiveEvidence:
    proof = build_lifecycle_delay_proof(
        request.run_id,
        EvidenceProvenance.FRESH_REAL,
        request.source_boundary,
        LifecycleDelayReduction((), (), (), True),
    )
    return AttributionLiveEvidence(proof, (), None)


def _instant(value: object) -> datetime:
    instant = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return instant


def _hash(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()

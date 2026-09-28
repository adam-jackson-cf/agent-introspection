"""Read-only E-Attribution-1 extractor for retained authority and current SQLite activity."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import chain
from pathlib import Path
from typing import cast

from experiments.dashboard_prototype.attribution_baseline import (
    AttributionBaselineProofInput,
    CanonicalActivityVersion,
    CapabilityState,
    LifecycleSessionObservation,
    ProducerAuthority,
    SourceSessionObservation,
    build_attribution_baseline_proof,
    p5_session_members,
)
from experiments.dashboard_prototype.attribution_common import AttributionExperimentId
from experiments.dashboard_prototype.attribution_live_common import (
    AttributionCalculationPrimitive,
    AttributionLiveEvidence,
    LiveProofRequest,
)
from experiments.dashboard_prototype.contracts import EvidenceProvenance

_IDENTITY_BOUNDARY = {
    "omp": ("getSessionId()", "session record id", "gen_ai.conversation.id"),
    "codex-cli": ("notify thread-id", "session_meta.payload.id", "thread.id or thread_id"),
}

_CANONICAL_ACTIVITY_EVENT = "introspection.activity.version.recorded"
_CANONICAL_ACTIVITY_PAYLOAD_SCHEMA_VERSION = 2
_INTERVAL_PRODUCERS = frozenset({"omp", "codex-app-server"})


def extract(
    connection: sqlite3.Connection, request: LiveProofRequest, *, retained_fixture: Path
) -> AttributionLiveEvidence:
    """Read retained authority and current latest versions without writing SQLite."""
    authorities = parse_retained_authorities(retained_fixture)
    activities = _current_activities(connection, request)
    sources = _current_sources(connection, request)
    lifecycles = _current_lifecycles(connection)
    proof = build_attribution_baseline_proof(
        run_id=request.run_id,
        provenance=EvidenceProvenance.FRESH_REAL,
        source_boundary=request.source_boundary,
        proof_input=AttributionBaselineProofInput(
            authorities=authorities,
            activities=activities,
            sources=sources,
            lifecycles=lifecycles,
            start=request.start,
            end=request.end,
        ),
    )
    primitives = _primitives(activities, sources, lifecycles, request)
    oracle = _oracle(primitives, activities_available=activities is not None)
    return AttributionLiveEvidence(
        proof,
        primitives,
        f"attribution-baseline-{request.run_id}" if primitives else None,
        oracle,
    )


@dataclass(frozen=True, slots=True)
class AttributionRowAuthority:
    """Bounded source/reducer authority for one concrete E1 row."""

    row_id: str
    producer: str
    native_session_id: str
    source_id: str
    reducer_id: str
    source_time: datetime
    expected_result: Mapping[str, str | int | bool | None]
    source_time_ns: int
    native_identity_bound: bool


def row_evidence_inputs(
    connection: sqlite3.Connection, request: LiveProofRequest
) -> tuple[AttributionRowAuthority, ...]:
    """Derive every bounded source and lifecycle member without collapsing either direction."""
    sources = _current_sources(connection, request)
    activities = _current_activities(connection, request)
    lifecycles = _current_lifecycles(connection)
    if sources is None or activities is None or lifecycles is None:
        return ()
    observations: chain[SourceSessionObservation | LifecycleSessionObservation] = chain(
        sources, lifecycles
    )
    native_identities = {(item.producer, item.native_session_id) for item in observations}
    rows: list[AttributionRowAuthority] = []
    for member in p5_session_members(sources, lifecycles, start=request.start, end=request.end):
        source_direction = member.direction == "source_to_lifecycle"
        if source_direction:
            if member.source is None or not member.source.source_id:
                raise ValueError("source session lacks its exact immutable source ID")
            source_id = member.source.source_id
        else:
            accepted = sorted(
                {
                    (instant, event_id)
                    for interval in member.intervals
                    for instant, event_id in interval.accepted_events
                    if request.start < instant <= request.end
                }
            )
            source_id = accepted[0][1]
        lineage = (
            member.producer,
            member.native_session_id,
            member.direction,
            member.source.source_id if member.source else None,
            [
                (
                    interval.lifecycle_event_id,
                    interval.interval_start.isoformat() if interval.interval_start else None,
                    interval.interval_end.isoformat() if interval.interval_end else None,
                )
                for interval in member.intervals
            ],
        )
        reducer_id = hashlib.sha256(json.dumps(lineage, separators=(",", ":")).encode()).hexdigest()
        rows.append(
            AttributionRowAuthority(
                "A07",
                member.producer,
                member.native_session_id,
                source_id,
                reducer_id,
                _source_datetime(member.source_time_ns),
                {
                    "source_sessions": int(source_direction),
                    "source_with_lifecycle": int(source_direction and member.matched),
                    "lifecycle_sessions": int(not source_direction),
                    "lifecycle_with_source": int(not source_direction and member.matched),
                },
                member.source_time_ns,
                True,
            )
        )
    for activity in activities:
        source_id = _digest(f"source:{activity.activity_id}")
        reducer_id = _digest(f"reducer:{activity.activity_id}:{activity.version}")
        rows.extend(
            (
                AttributionRowAuthority(
                    "A08",
                    activity.producer,
                    activity.native_session_id,
                    source_id,
                    reducer_id,
                    _source_datetime(activity.source_time_ns),
                    {
                        "eligible": 1,
                        "attributed": int(activity.state == "attributed"),
                        "unresolved": int(activity.state == "unresolved"),
                        "project_digest": _digest(activity.project_id)
                        if activity.project_id is not None
                        else "none",
                    },
                    activity.source_time_ns,
                    (activity.producer, activity.native_session_id) in native_identities,
                ),
                AttributionRowAuthority(
                    "A09",
                    activity.producer,
                    activity.native_session_id,
                    source_id,
                    reducer_id,
                    _source_datetime(activity.source_time_ns),
                    {
                        "eligible": 1,
                        "unresolved": int(activity.state == "unresolved"),
                        "diagnostic_count": int(activity.state == "unresolved"),
                    },
                    activity.source_time_ns,
                    (activity.producer, activity.native_session_id) in native_identities,
                ),
            )
        )
    return tuple(rows)


def parse_retained_authorities(path: Path) -> tuple[ProducerAuthority, ...]:
    """Parse only OMP/Codex CLI proof fields; roots and raw identities never escape."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    policy = payload.get("evidence_policy")
    if not isinstance(policy, Mapping) or policy.get("identifiers_and_counts_only") is not True:
        raise ValueError("retained proof lacks its privacy evidence policy")
    result: list[ProducerAuthority] = []
    for entry in payload.get("supported", ()):
        if not isinstance(entry, Mapping) or entry.get("producer") not in {"omp", "codex-cli"}:
            continue
        producer = str(entry["producer"])
        correlation = entry.get("correlation_id")
        local_ids = entry.get("local_artifact_ids")
        project = entry.get("project")
        scenarios = entry.get("scenarios")
        if (
            not isinstance(correlation, str)
            or not correlation
            or not isinstance(local_ids, list)
            or not local_ids
        ):
            raise ValueError("retained proof lacks immutable correlation evidence")
        if any(not isinstance(identity, str) or len(identity) != 64 for identity in local_ids):
            raise ValueError("retained proof local evidence identities are not immutable hashes")
        if not isinstance(project, Mapping) or not isinstance(scenarios, Mapping):
            raise ValueError("retained proof lacks project or scenario authority")
        expected_boundary = _IDENTITY_BOUNDARY[producer]
        if (
            entry.get("native_lifecycle_field") != expected_boundary[0]
            or entry.get("local_artifact_field") != expected_boundary[1]
            or entry.get("otel_field") != expected_boundary[2]
        ):
            raise ValueError("retained proof violates the exact native identity boundary")
        capabilities = {
            name: _capability(scenarios.get(name))
            for name in (
                "fresh",
                "resume",
                "end",
                "concurrent_projects",
                "non_git",
                "workspace_change",
            )
        }
        # Correlation declares the native identity; local IDs are immutable artifacts.
        result.append(
            ProducerAuthority(
                producer=producer,
                surface=producer,
                evidence_id=_digest(
                    f"{producer}:{correlation}:{','.join(sorted(map(str, local_ids)))}"
                ),
                native_matches_correlation=True,
                project_id=str(project.get("id", "")),
                project_name=str(project.get("name", "")),
                project_kind=str(project.get("kind", "")),
                capabilities=capabilities,
            )
        )
    if {row.producer for row in result} != {"omp", "codex-cli"}:
        raise ValueError("retained proof must contain exactly OMP and Codex CLI authority")
    return tuple(sorted(result, key=lambda row: row.producer))


def _current_sources(
    connection: sqlite3.Connection, request: LiveProofRequest
) -> tuple[SourceSessionObservation, ...] | None:
    if not _has_columns(
        connection,
        "source_session_current",
        {
            "native_producer",
            "native_session_id",
            "source_id",
            "source_timestamp",
            "source_timestamp_ns",
        },
    ):
        return None
    start_ns, end_ns = _epoch_ns(request.start), _epoch_ns(request.end)
    rows = connection.execute(
        """
        SELECT native_producer, native_session_id, source_id, source_timestamp_ns
        FROM source_session_current
        WHERE native_producer = 'omp'
          AND (
            (CAST(source_timestamp_ns AS INTEGER) > ?
             AND CAST(source_timestamp_ns AS INTEGER) <= ?)
            OR (source_timestamp >= ? AND source_timestamp <= ?)
          )
        ORDER BY source_id
        """,
        (start_ns, end_ns, request.start.isoformat(), request.end.isoformat()),
    ).fetchall()
    observations: list[SourceSessionObservation] = []
    for producer, session_id, source_id, timestamp_ns in rows:
        # Coarse datetime bounds retain indeterminate boundary records; they never
        # supply a raw timestamp or establish selected-source membership.
        if not isinstance(timestamp_ns, str) or not timestamp_ns.isascii():
            return None
        if not timestamp_ns.isdecimal():
            return None
        source_ns = int(timestamp_ns)
        if not start_ns < source_ns <= end_ns:
            continue
        if not all(isinstance(value, str) and value for value in (session_id, source_id)):
            return None
        observations.append(
            SourceSessionObservation(producer, producer, session_id, source_ns, source_id)
        )
    return tuple(observations)


def _current_lifecycles(
    connection: sqlite3.Connection,
) -> tuple[LifecycleSessionObservation, ...] | None:
    if (
        not _has_columns(
            connection,
            "session_context_intervals",
            {"event_id", "end_event_id", "producer", "session_id", "started_at", "ended_at"},
        )
        or not _has_columns(
            connection,
            "session_context_events",
            {"event_id", "producer", "session_id", "event_type", "occurred_at"},
        )
        or not _has_columns(
            connection,
            "session_context_event_supersessions",
            {"original_event_id", "replacement_event_id"},
        )
    ):
        return None
    accepted_events: dict[tuple[str, str], list[tuple[datetime, str]]] = {}
    starts: dict[tuple[str, str, str], datetime] = {}
    ends: dict[tuple[str, str, str], datetime] = {}
    for event in connection.execute(
        """
        SELECT event.event_id, event.producer, event.session_id, event.event_type, event.occurred_at
        FROM session_context_events AS event
        LEFT JOIN session_context_event_supersessions AS supersession
          ON supersession.original_event_id = event.event_id
        WHERE event.producer = 'omp'
          AND supersession.original_event_id IS NULL
        """
    ):
        event_time = _instant(str(event[4]))
        if event_time is None:
            return None
        producer, session_id = str(event[1]), str(event[2])
        accepted_events.setdefault((producer, session_id), []).append((event_time, str(event[0])))
        if str(event[3]) in {"session_start", "workspace_changed"}:
            starts[(str(event[0]), producer, session_id)] = event_time
        if str(event[3]) in {"workspace_changed", "session_end"}:
            ends[(str(event[0]), producer, session_id)] = event_time
    rows = connection.execute(
        """
        SELECT interval.event_id, interval.producer, interval.session_id,
               interval.started_at, interval.ended_at, interval.end_event_id
        FROM session_context_intervals AS interval
        LEFT JOIN session_context_event_supersessions AS opening_supersession
          ON opening_supersession.original_event_id = interval.event_id
        LEFT JOIN session_context_event_supersessions AS ending_supersession
          ON ending_supersession.original_event_id = interval.end_event_id
        WHERE interval.producer = 'omp'
          AND opening_supersession.original_event_id IS NULL
          AND ending_supersession.original_event_id IS NULL
        """
    ).fetchall()
    observations: list[LifecycleSessionObservation] = []
    for row in rows:
        event_id, producer, session_id = str(row[0]), str(row[1]), str(row[2])
        if producer not in _INTERVAL_PRODUCERS:
            return None
        start = _instant(str(row[3]))
        end = _instant(str(row[4])) if row[4] else None
        if (
            start is None
            or (end is not None and end < start)
            or starts.get((event_id, producer, session_id)) != start
            or (row[4] is not None and end is None)
            or (end is None) != (row[5] is None)
            or (end is not None and ends.get((str(row[5]), producer, session_id)) != end)
        ):
            return None
        observations.append(
            LifecycleSessionObservation(
                producer,
                producer,
                session_id,
                start,
                end,
                tuple(sorted(accepted_events[(producer, session_id)])),
                event_id,
            )
        )
    interval_keys = {(row.producer, row.native_session_id) for row in observations}
    for producer, session_id in sorted(accepted_events.keys() - interval_keys):
        observations.append(
            LifecycleSessionObservation(
                producer,
                producer,
                session_id,
                None,
                None,
                tuple(sorted(accepted_events[(producer, session_id)])),
            )
        )
    return tuple(observations)


def _has_columns(connection: sqlite3.Connection, table: str, required: set[str]) -> bool:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    if exists is None:
        return False
    columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
    return required <= columns


def _current_activities(
    connection: sqlite3.Connection, request: LiveProofRequest
) -> tuple[CanonicalActivityVersion, ...] | None:
    required = {
        "canonical_activities",
        "canonical_activity_versions",
        "canonical_activity_outbox_evidence",
        "otlp_outbox",
        "session_context_intervals",
        "session_context_events",
        "session_context_event_supersessions",
    }
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if not required <= tables:
        return None
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(canonical_activities)")}
    version_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(canonical_activity_versions)")
    }
    interval_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(session_context_intervals)")
    }
    event_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(session_context_events)")
    }
    evidence_columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(canonical_activity_outbox_evidence)")
    }
    outbox_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(otlp_outbox)")}
    if (
        not {"id", "producer", "producer_surface", "correlation_id", "source_ended_at_ns"}
        <= columns
        or not {
            "activity_id",
            "version",
            "attribution_state",
            "project_identity_id",
            "attribution_method",
            "reason_code",
        }
        <= version_columns
        or not {
            "producer",
            "session_id",
            "started_at",
            "ended_at",
            "event_id",
            "end_event_id",
            "project_id",
        }
        <= interval_columns
        or not {"event_id", "producer", "session_id", "event_type", "project_id"} <= event_columns
        or not {
            "activity_id",
            "activity_version",
            "payload_schema_version",
            "event_name",
            "event_id",
        }
        <= evidence_columns
        or not {"event_id", "payload_json"} <= outbox_columns
    ):
        return None
    rows = connection.execute(
        """
        SELECT activity.id, activity.producer, activity.producer_surface, activity.correlation_id,
               activity.source_ended_at_ns, version.version, version.attribution_state,
               version.project_identity_id, version.attribution_method, version.reason_code,
               evidence.event_id, evidence.payload_schema_version, evidence.event_name,
               outbox.payload_json
        FROM canonical_activities AS activity
        JOIN canonical_activity_versions AS version ON version.activity_id = activity.id
        LEFT JOIN canonical_activity_outbox_evidence AS evidence
          ON evidence.activity_id = version.activity_id
         AND evidence.activity_version = version.version
         AND evidence.payload_schema_version = ?
         AND evidence.event_name = ?
        LEFT JOIN otlp_outbox AS outbox ON outbox.event_id = evidence.event_id
        WHERE activity.producer IN ('omp', 'codex-cli')
        ORDER BY activity.id, version.version, evidence.event_id
        """,
        (
            _CANONICAL_ACTIVITY_PAYLOAD_SCHEMA_VERSION,
            _CANONICAL_ACTIVITY_EVENT,
        ),
    ).fetchall()
    histories: dict[str, list[tuple[object, ...]]] = {}
    for row in rows:
        histories.setdefault(str(row[0]), []).append(row)
    result: list[CanonicalActivityVersion] = []
    for activity_id, history in sorted(histories.items()):
        if (
            any(type(row[5]) is not int for row in history)
            or [row[5] for row in history] != list(range(1, len(history) + 1))
            or any(row[1:5] != history[0][1:5] for row in history)
            or any(not _valid_activity_envelope(row) for row in history)
            or type(history[0][4]) is not int
        ):
            return None
        if not _epoch_ns(request.start) < history[0][4] <= _epoch_ns(request.end):
            continue
        latest = history[-1]
        try:
            source_time = _source_datetime(cast(int, latest[4]))
            context = _activity_context(connection, latest, source_time)
        except (OverflowError, OSError, ValueError):
            return None
        if context is None or (
            latest[6] == "resolved" and (len(context) != 1 or latest[7] != context[0])
        ):
            return None
        state = "attributed" if latest[6] == "resolved" else "unresolved"
        reason = None if state == "attributed" else _reason(latest[9])
        result.append(
            CanonicalActivityVersion(
                activity_id,
                cast(int, latest[5]),
                cast(int, latest[4]),
                str(latest[1]),
                str(latest[2]),
                str(latest[3]),
                state,
                _digest(str(latest[7])) if state == "attributed" else None,
                reason,
                str(latest[8]) if latest[8] else "none",
            )
        )
    return tuple(result)


def _activity_context(
    connection: sqlite3.Connection, row: tuple[object, ...], source_time: datetime
) -> tuple[str, ...] | None:
    producer, session_id = str(row[1]), str(row[3])
    if producer == "codex-cli":
        points = connection.execute(
            """
            SELECT event.project_id
            FROM session_context_events AS event
            LEFT JOIN session_context_event_supersessions AS supersession
              ON supersession.original_event_id = event.event_id
            WHERE event.producer = 'codex-cli' AND event.session_id = ?
              AND event.event_type = 'session_context'
              AND supersession.original_event_id IS NULL
            """,
            (session_id,),
        ).fetchall()
        if any(not _valid_project_id(point[0]) for point in points):
            return None
        projects = {str(point[0]) for point in points}
        return tuple(sorted(projects))
    if producer not in _INTERVAL_PRODUCERS:
        return None
    intervals = connection.execute(
        """
        SELECT interval.project_id FROM session_context_intervals AS interval
        LEFT JOIN session_context_event_supersessions AS opening_supersession
          ON opening_supersession.original_event_id = interval.event_id
        LEFT JOIN session_context_event_supersessions AS ending_supersession
          ON ending_supersession.original_event_id = interval.end_event_id
        WHERE interval.producer = ? AND interval.session_id = ? AND interval.started_at <= ?
          AND (interval.ended_at IS NULL OR ? < interval.ended_at)
          AND opening_supersession.original_event_id IS NULL
          AND ending_supersession.original_event_id IS NULL
        ORDER BY interval.started_at, interval.event_id
        """,
        (producer, session_id, source_time.isoformat(), source_time.isoformat()),
    ).fetchall()
    if any(not _valid_project_id(interval[0]) for interval in intervals):
        return None
    return tuple(str(interval[0]) for interval in intervals)


def _valid_activity_envelope(row: tuple[object, ...]) -> bool:
    try:
        activity_id, producer, surface, correlation_id, source_time_ns, version = row[:6]
        event_id, payload_schema_version, event_name, payload_json = row[10:14]
        if (
            not isinstance(activity_id, str)
            or not isinstance(producer, str)
            or not isinstance(surface, str)
            or not isinstance(correlation_id, str)
            or type(source_time_ns) is not int
            or source_time_ns < 0
            or type(version) is not int
            or row[6] not in {"resolved", "unresolved"}
            or not isinstance(row[8], str)
            or (row[6] == "resolved" and (not _valid_project_id(row[7]) or row[9] is not None))
            or (
                row[6] == "unresolved"
                and (row[7] is not None or not isinstance(row[9], str) or not row[9])
            )
            or type(payload_schema_version) is not int
            or payload_schema_version != _CANONICAL_ACTIVITY_PAYLOAD_SCHEMA_VERSION
            or event_name != _CANONICAL_ACTIVITY_EVENT
            or event_id
            != hashlib.sha256(
                "\x1f".join(
                    (
                        activity_id,
                        str(version),
                        str(payload_schema_version),
                        str(event_name),
                    )
                ).encode()
            ).hexdigest()
        ):
            return False
        payload = json.loads(str(payload_json))
        expected = {
            "event.id": event_id,
            "event.scope": "canonical-activity",
            "event.name": event_name,
            "activity.id": activity_id,
            "activity.version": version,
            "activity.payload_schema_version": payload_schema_version,
            "timestamp_ns": source_time_ns,
            "activity.producer": producer,
            "activity.producer_surface": surface,
            "activity.correlation_id": correlation_id,
            "activity.attribution.state": row[6],
            "activity.attribution.method": row[8],
        }
        if not isinstance(payload, dict) or any(
            type(payload.get(key)) is not type(value) or payload[key] != value
            for key, value in expected.items()
        ):
            return False
        if row[6] == "resolved":
            return (
                payload.get("agent.project.id") == row[7]
                and payload.get("activity.attribution.project_identity_id") == row[7]
                and "activity.attribution.reason_code" not in payload
            )
        return (
            row[6] == "unresolved"
            and row[7] is None
            and payload.get("agent.project.id") == "unresolved"
            and payload.get("activity.attribution.reason_code") == row[9]
            and "activity.attribution.project_identity_id" not in payload
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return False


def _valid_project_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _source_datetime(source_time_ns: int) -> datetime:
    seconds, nanoseconds = divmod(source_time_ns, 1_000_000_000)
    return datetime.fromtimestamp(seconds, UTC).replace(microsecond=nanoseconds // 1_000)


def _primitives(
    activities: tuple[CanonicalActivityVersion, ...] | None,
    sources: tuple[SourceSessionObservation, ...] | None,
    lifecycles: tuple[LifecycleSessionObservation, ...] | None,
    request: LiveProofRequest,
) -> tuple[AttributionCalculationPrimitive, ...]:
    activity_primitives = tuple(
        AttributionCalculationPrimitive(
            AttributionExperimentId.BASELINE,
            _source_datetime(row.source_time_ns),
            ordinal,
            {
                "producer": row.producer,
                "surface": row.surface,
                "outcome": row.state,
                "primitive_kind": "p7_p8",
                "diagnostic": row.reason_code or "none",
                "method": row.attribution_method,
                "project_digest": row.project_id or "none",
            },
            {"count": 1},
            source_time_ns=row.source_time_ns,
        )
        for ordinal, row in enumerate(activities if activities is not None else ())
        if _epoch_ns(request.start) < row.source_time_ns <= _epoch_ns(request.end)
    )
    p5_primitives = tuple(
        AttributionCalculationPrimitive(
            AttributionExperimentId.BASELINE,
            _source_datetime(member.source_time_ns),
            len(activity_primitives) + ordinal,
            {
                "producer": member.producer,
                "surface": member.surface,
                "primitive_kind": "p5",
                "direction": member.direction,
                "matched": member.matched,
            },
            {"count": 1},
            source_time_ns=member.source_time_ns,
        )
        for ordinal, member in enumerate(
            p5_session_members(sources, lifecycles, start=request.start, end=request.end)
            if sources is not None and lifecycles is not None
            else ()
        )
    )
    return activity_primitives + tuple(p5_primitives)


def _oracle(
    primitives: tuple[AttributionCalculationPrimitive, ...],
    *,
    activities_available: bool,
) -> dict[str, dict[str, int]]:
    p7_p8 = [row for row in primitives if row.dimensions["primitive_kind"] == "p7_p8"]
    p5: dict[str, dict[str, int]] = {}
    for row in primitives:
        if row.dimensions["primitive_kind"] != "p5":
            continue
        key = f"p5.{row.dimensions['producer']}.{row.dimensions['surface']}"
        cohort = p5.setdefault(
            key,
            {
                "source_sessions": 0,
                "source_with_lifecycle": 0,
                "lifecycle_sessions": 0,
                "lifecycle_with_source": 0,
            },
        )
        if row.dimensions["direction"] == "source_to_lifecycle":
            cohort["source_sessions"] += 1
            if row.dimensions["matched"] is True:
                cohort["source_with_lifecycle"] += 1
        else:
            cohort["lifecycle_sessions"] += 1
            if row.dimensions["matched"] is True:
                cohort["lifecycle_with_source"] += 1
    p7_cohorts: dict[str, dict[str, int]] = {}
    p8_groups: dict[str, dict[str, int]] = {}
    for row in p7_p8:
        dimensions = row.dimensions
        p7_key = f"p7.{dimensions['producer']}.{dimensions['surface']}"
        cohort = p7_cohorts.setdefault(
            p7_key, {"eligible": 0, "attributed": 0, "unresolved": 0, "distinct_projects": 0}
        )
        cohort["eligible"] += 1
        cohort[str(dimensions["outcome"])] += 1
        p8_key = (
            f"p8.{dimensions['producer']}.{dimensions['surface']}."
            f"{dimensions['method']}.{dimensions['outcome']}.{dimensions['diagnostic']}"
        )
        p8_groups[p8_key] = {"count": p8_groups.get(p8_key, {"count": 0})["count"] + 1}
    for key, cohort in p7_cohorts.items():
        producer, surface = key.removeprefix("p7.").split(".", maxsplit=1)
        projects = {
            row.dimensions["project_digest"]
            for row in p7_p8
            if row.dimensions["producer"] == producer
            and row.dimensions["surface"] == surface
            and row.dimensions["project_digest"] != "none"
        }
        cohort["distinct_projects"] = len(projects)
    if not activities_available:
        return p5
    return {
        "p7": {
            "eligible": len(p7_p8),
            "attributed": sum(row.dimensions["outcome"] == "attributed" for row in p7_p8),
            "unresolved": sum(row.dimensions["outcome"] == "unresolved" for row in p7_p8),
            "distinct_projects": len(
                {
                    row.dimensions["project_digest"]
                    for row in p7_p8
                    if row.dimensions["project_digest"] != "none"
                }
            ),
        },
        "p8": {"total": sum(group["count"] for group in p8_groups.values())},
        **p5,
        **p7_cohorts,
        **p8_groups,
    }


def _capability(value: object) -> CapabilityState:
    if value == "passed":
        return CapabilityState.PASSED
    if isinstance(value, str) and value.startswith("not_exposed"):
        return CapabilityState.NOT_EXPOSED
    raise ValueError("retained scenario capability is contradictory")


def _reason(value: object) -> str:
    candidate = str(value) if value else "no_authoritative_context"
    return candidate if candidate.replace("_", "").isalnum() else "invalid_reason"


def _epoch_ns(value: datetime) -> int:
    delta = value.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _instant(value: str) -> datetime | None:
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return instant if instant.tzinfo is not None else None


def _metric_int(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise ValueError("P5 metric must be an integer")

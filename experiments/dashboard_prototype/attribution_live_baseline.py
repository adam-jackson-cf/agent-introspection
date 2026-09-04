"""Read-only E-Attribution-1 extractor for retained authority and current SQLite activity."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from experiments.dashboard_prototype.attribution_baseline import (
    AttributionBaselineProofInput,
    CanonicalActivityVersion,
    CapabilityState,
    LifecycleSessionObservation,
    ProducerAuthority,
    SourceSessionObservation,
    build_attribution_baseline_proof,
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
    primitives = _primitives(activities or (), sources or (), lifecycles or (), request)
    oracle = _oracle(primitives) if primitives else {}
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


def row_evidence_inputs(
    connection: sqlite3.Connection, request: LiveProofRequest
) -> tuple[AttributionRowAuthority, ...]:
    """Derive row authority only from bounded source and reducer records."""
    sources = _current_sources(connection, request)
    activities = _current_activities(connection, request)
    lifecycles = _current_lifecycles(connection)
    if sources is None or activities is None or lifecycles is None:
        return ()
    rows: list[AttributionRowAuthority] = []
    for source in sources:
        matched = any(
            lifecycle.producer == source.producer
            and lifecycle.surface == source.surface
            and lifecycle.native_session_id == source.native_session_id
            and lifecycle.interval_start <= source.source_time
            and (lifecycle.interval_end is None or source.source_time < lifecycle.interval_end)
            for lifecycle in lifecycles
        )
        if request.start < source.source_time <= request.end and matched:
            rows.append(
                AttributionRowAuthority(
                    "A07",
                    source.producer,
                    source.native_session_id,
                    _digest(f"source:{source.producer}:{source.native_session_id}"),
                    _digest(
                        f"reducer:{source.producer}:{source.native_session_id}:{source.source_time.isoformat()}"
                    ),
                    source.source_time,
                    {
                        "source_sessions": 1,
                        "source_with_lifecycle": 1,
                        "lifecycle_sessions": 1,
                        "lifecycle_with_source": 1,
                    },
                )
            )
    for activity in activities:
        if not request.start < activity.source_time <= request.end:
            continue
        source_id = _digest(f"source:{activity.producer}:{activity.native_session_id}")
        reducer_id = _digest(f"reducer:{activity.activity_id}:{activity.version}")
        rows.extend(
            (
                AttributionRowAuthority(
                    "A08",
                    activity.producer,
                    activity.native_session_id,
                    source_id,
                    reducer_id,
                    activity.source_time,
                    {
                        "eligible": 1,
                        "attributed": int(activity.state == "attributed"),
                        "unresolved": int(activity.state == "unresolved"),
                        "distinct_projects": int(activity.project_id is not None),
                    },
                ),
                AttributionRowAuthority(
                    "A09",
                    activity.producer,
                    activity.native_session_id,
                    source_id,
                    reducer_id,
                    activity.source_time,
                    {
                        "eligible": 1,
                        "unresolved": int(activity.state == "unresolved"),
                        "diagnostic_count": int(activity.state == "unresolved"),
                        "diagnostic": activity.reason_code,
                    },
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
        {"native_producer", "native_session_id", "source_timestamp"},
    ):
        return None
    rows = connection.execute(
        """
        SELECT native_producer, native_session_id, MIN(source_timestamp)
        FROM source_session_current
        WHERE native_producer IN ('omp', 'codex-cli')
          AND native_session_id IS NOT NULL
          AND source_timestamp > ? AND source_timestamp <= ?
        GROUP BY native_producer, native_session_id
        """,
        (request.start.isoformat(), request.end.isoformat()),
    ).fetchall()
    observations: list[SourceSessionObservation] = []
    for row in rows:
        source_time = _instant(str(row[2]))
        if source_time is not None:
            observations.append(
                SourceSessionObservation(
                    str(row[0]), str(row[0]), _digest(str(row[1])), source_time
                )
            )
    return tuple(observations)


def _current_lifecycles(
    connection: sqlite3.Connection,
) -> tuple[LifecycleSessionObservation, ...] | None:
    if not _has_columns(
        connection,
        "session_context_intervals",
        {"producer", "session_id", "started_at", "ended_at"},
    ):
        return None
    event_times: dict[tuple[str, str], list[datetime]] = {}
    if _has_columns(
        connection,
        "session_context_events",
        {"producer", "session_id", "occurred_at"},
    ):
        for event in connection.execute(
            "SELECT producer, session_id, occurred_at FROM session_context_events "
            "WHERE producer IN ('omp', 'codex-cli')"
        ):
            event_time = _instant(str(event[2]))
            if event_time is not None:
                event_times.setdefault((str(event[0]), str(event[1])), []).append(event_time)
    rows = connection.execute(
        "SELECT producer, session_id, started_at, ended_at FROM session_context_intervals "
        "WHERE producer IN ('omp', 'codex-cli')"
    ).fetchall()
    observations: list[LifecycleSessionObservation] = []
    for row in rows:
        start = _instant(str(row[2]))
        end = _instant(str(row[3])) if row[3] else None
        if start is not None:
            events = tuple(event_times.get((str(row[0]), str(row[1])), [start]))
            observations.append(
                LifecycleSessionObservation(
                    str(row[0]), str(row[0]), _digest(str(row[1])), start, end, events
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
        "session_context_intervals",
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
    needed = {
        "id",
        "producer",
        "producer_surface",
        "correlation_id",
        "source_ended_at_ns",
    }
    needed_versions = {
        "activity_id",
        "version",
        "attribution_state",
        "project_identity_id",
        "attribution_method",
        "reason_code",
    }
    needed_intervals = {
        "producer",
        "session_id",
        "started_at",
        "ended_at",
        "event_id",
        "project_id",
    }
    if (
        not needed <= columns
        or not needed_versions <= version_columns
        or not needed_intervals <= interval_columns
    ):
        return None
    rows = connection.execute(
        """
        WITH ranked AS (
          SELECT activity.id, activity.producer, activity.producer_surface, activity.correlation_id,
                 activity.source_ended_at_ns, version.version, version.attribution_state,
                 version.project_identity_id, version.attribution_method, version.reason_code,
                 row_number() OVER (PARTITION BY activity.id ORDER BY version.version DESC) AS rank
          FROM canonical_activities AS activity
          JOIN canonical_activity_versions AS version ON version.activity_id = activity.id
          WHERE activity.producer IN ('omp', 'codex-cli')
            AND activity.source_ended_at_ns > ? AND activity.source_ended_at_ns <= ?
        ) SELECT * FROM ranked WHERE rank = 1 ORDER BY source_ended_at_ns, id
        """,
        (
            int(request.start.timestamp() * 1_000_000_000),
            int(request.end.timestamp() * 1_000_000_000),
        ),
    ).fetchall()
    result: list[CanonicalActivityVersion] = []
    for row in rows:
        source_time = datetime.fromtimestamp(int(row[4]) / 1_000_000_000, tz=request.start.tzinfo)
        interval = connection.execute(
            """
            SELECT project_id FROM session_context_intervals
            WHERE producer = ? AND session_id = ? AND started_at <= ?
              AND (ended_at IS NULL OR ? < ended_at)
            ORDER BY started_at, event_id
            LIMIT 1
            """,
            (row[1], row[3], source_time.isoformat(), source_time.isoformat()),
        ).fetchone()
        matched_project = interval and row[7] == interval[0]
        state = "attributed" if row[6] == "resolved" and matched_project else "unresolved"
        reason = None if state == "attributed" else _reason(row[9])
        if state == "unresolved" and interval is None:
            reason = "no_authoritative_context"
        result.append(
            CanonicalActivityVersion(
                _digest(str(row[0])),
                int(row[5]),
                source_time,
                str(row[1]),
                str(row[2]),
                _digest(str(row[3])),
                state,
                _digest(str(row[7])) if state == "attributed" else None,
                reason,
                str(row[8]) if row[8] else "none",
            )
        )
    return tuple(result)


def _primitives(
    activities: tuple[CanonicalActivityVersion, ...],
    sources: tuple[SourceSessionObservation, ...],
    lifecycles: tuple[LifecycleSessionObservation, ...],
    request: LiveProofRequest,
) -> tuple[AttributionCalculationPrimitive, ...]:
    activity_primitives = tuple(
        AttributionCalculationPrimitive(
            AttributionExperimentId.BASELINE,
            row.source_time,
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
        )
        for ordinal, row in enumerate(activities)
        if request.start < row.source_time <= request.end
    )
    p5_primitives: list[AttributionCalculationPrimitive] = []
    ordinal = len(activity_primitives)
    for source in sources:
        matched = any(
            lifecycle.producer == source.producer
            and lifecycle.surface == source.surface
            and lifecycle.native_session_id == source.native_session_id
            and lifecycle.interval_start <= source.source_time
            and (lifecycle.interval_end is None or source.source_time < lifecycle.interval_end)
            for lifecycle in lifecycles
        )
        p5_primitives.append(
            AttributionCalculationPrimitive(
                AttributionExperimentId.BASELINE,
                source.source_time,
                ordinal,
                {
                    "producer": source.producer,
                    "surface": source.surface,
                    "primitive_kind": "p5",
                    "direction": "source_to_lifecycle",
                    "matched": matched,
                },
                {"count": 1},
            )
        )
        ordinal += 1
    lifecycle_groups: dict[tuple[str, str, str], list[LifecycleSessionObservation]] = {}
    for lifecycle in lifecycles:
        lifecycle_groups.setdefault(
            (lifecycle.producer, lifecycle.surface, lifecycle.native_session_id), []
        ).append(lifecycle)
    for key, intervals in sorted(lifecycle_groups.items()):
        event_times = sorted(
            event_time
            for lifecycle in intervals
            for event_time in lifecycle.accepted_event_times
            if request.start < event_time <= request.end
        )
        if not event_times:
            continue
        matched = any(
            source.producer == key[0]
            and source.surface == key[1]
            and source.native_session_id == key[2]
            and any(
                interval.interval_start <= source.source_time
                and (interval.interval_end is None or source.source_time < interval.interval_end)
                for interval in intervals
            )
            for source in sources
        )
        p5_primitives.append(
            AttributionCalculationPrimitive(
                AttributionExperimentId.BASELINE,
                event_times[0],
                ordinal,
                {
                    "producer": key[0],
                    "surface": key[1],
                    "primitive_kind": "p5",
                    "direction": "lifecycle_to_source",
                    "matched": matched,
                },
                {"count": 1},
            )
        )
        ordinal += 1
    return activity_primitives + tuple(p5_primitives)


def _oracle(
    primitives: tuple[AttributionCalculationPrimitive, ...],
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

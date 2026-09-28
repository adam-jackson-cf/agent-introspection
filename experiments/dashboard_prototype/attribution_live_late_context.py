"""Read-only SQLite extraction for E-Attribution-5 late context."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime

from experiments.dashboard_prototype.attribution_common import AttributionExperimentId
from experiments.dashboard_prototype.attribution_late_context import (
    ActivityVersion,
    LateContextTransition,
    build_late_context_proof,
    reduce_late_context,
)
from experiments.dashboard_prototype.attribution_live_common import (
    AttributionCalculationPrimitive,
    AttributionLiveEvidence,
    LiveProofRequest,
)
from experiments.dashboard_prototype.contracts import EvidenceProvenance, ExperimentResult

_REMOTE_QUERY_ID = "attribution-late-context-v1"
_CANONICAL_EVENT_NAME = "introspection.activity.version.recorded"
_CANONICAL_PAYLOAD_SCHEMA_VERSION = 2
_REQUIRED_TABLES = frozenset(
    {
        "canonical_activities",
        "canonical_activity_versions",
        "canonical_activity_outbox_evidence",
        "session_context_intervals",
    }
)
_SUPPORTED_PAIRS = frozenset(
    {("omp", "omp"), ("codex-cli", "codex-cli"), ("codex-app-server", "codex-app-server")}
)


def extract(connection: sqlite3.Connection, request: LiveProofRequest) -> AttributionLiveEvidence:
    """Extract complete selected histories without exposing native IDs or paths."""
    missing = _missing_tables(connection)
    if missing:
        return _blocked(request, tuple(f"sqlite.schema.{name}" for name in missing))
    rows = tuple(
        connection.execute(
            """
            WITH selected_activity_ids AS (
                SELECT DISTINCT activity.id
                FROM canonical_activities AS activity
                JOIN canonical_activity_versions AS version ON version.activity_id = activity.id
                JOIN canonical_activity_outbox_evidence AS evidence
                  ON evidence.activity_id = version.activity_id
                 AND evidence.activity_version = version.version
                 AND evidence.payload_schema_version = ?
                 AND evidence.event_name = ?
                WHERE activity.producer IN ('omp', 'codex-cli', 'codex-app-server')
                  AND activity.source_ended_at_ns > ?
                  AND activity.source_ended_at_ns <= ?
            )
            SELECT a.id, a.producer, a.producer_surface, a.correlation_id,
                   a.source_ended_at_ns, av.version, av.attribution_state,
                   av.attribution_method, av.reason_code, av.project_identity_id,
                   evidence.event_id, evidence.payload_schema_version, evidence.event_name
            FROM selected_activity_ids AS selected
            JOIN canonical_activities AS a ON a.id = selected.id
            JOIN canonical_activity_versions AS av ON av.activity_id = a.id
            LEFT JOIN canonical_activity_outbox_evidence AS evidence
              ON evidence.activity_id = av.activity_id
             AND evidence.activity_version = av.version
             AND evidence.payload_schema_version = ?
             AND evidence.event_name = ?
            ORDER BY a.id, av.version, evidence.event_id
            """,
            (
                _CANONICAL_PAYLOAD_SCHEMA_VERSION,
                _CANONICAL_EVENT_NAME,
                _epoch_ns(request.start),
                _epoch_ns(request.end),
                _CANONICAL_PAYLOAD_SCHEMA_VERSION,
                _CANONICAL_EVENT_NAME,
            ),
        )
    )
    selected_activity_ids = {str(row[0]) for row in rows}
    intervals = tuple(
        connection.execute(
            """
            SELECT producer, session_id, started_at, ended_at
            FROM session_context_intervals
            WHERE producer IN ('omp', 'codex-cli', 'codex-app-server')
            ORDER BY producer, session_id, started_at, event_id
            """
        )
    )
    versions, authority_missing = _versions(rows, intervals, selected_activity_ids)
    reduction = reduce_late_context(versions, start=request.start, end=request.end)
    proof = build_late_context_proof(
        request.run_id,
        EvidenceProvenance.FRESH_REAL,
        request.source_boundary,
        reduction,
        remote_denominator=None,
    )
    if authority_missing:
        proof = replace(
            proof,
            result=ExperimentResult.BLOCKED,
            blocked_boundaries=tuple(
                sorted(set(proof.blocked_boundaries) | {"late-context.lifecycle-authority"})
            ),
        )
    denominator_members = {
        version.activity_id: version
        for version in versions
        if version.attribution_state == "unresolved"
    }
    denominator_primitives = tuple(
        AttributionCalculationPrimitive(
            experiment_id=AttributionExperimentId.LATE_CONTEXT,
            source_time=_source_datetime(version.source_time_ns),
            ordinal=ordinal,
            dimensions={
                "primitive_kind": "ever_unresolved",
                "activity_hash": version.activity_hash,
                "cohort": "ever_unresolved",
            },
            measures={"ever_unresolved_count": 1},
            source_time_ns=version.source_time_ns,
        )
        for ordinal, version in enumerate(
            sorted(denominator_members.values(), key=lambda item: item.activity_id)
        )
    )
    transition_primitives = tuple(
        AttributionCalculationPrimitive(
            experiment_id=AttributionExperimentId.LATE_CONTEXT,
            source_time=_source_datetime(transition.source_time_ns),
            ordinal=len(denominator_primitives) + ordinal,
            dimensions={
                "primitive_kind": "transition",
                "producer": transition.producer,
                "surface": transition.surface,
                "method": transition.attribution_method,
                "project_id": transition.project_id,
                "activity_hash": transition.activity_hash,
                "resolved_event_digest": transition.resolved_event_id,
                "prior_reason": transition.prior_reason_code or "none",
            },
            measures={"transition_count": 1},
            source_time_ns=transition.source_time_ns,
        )
        for ordinal, transition in enumerate(reduction.transitions)
    )
    primitives = denominator_primitives + transition_primitives if not authority_missing else ()
    oracle = _transition_oracle(reduction.transitions) if not authority_missing else {}
    if primitives:
        oracle = {
            **oracle,
            "ever_unresolved": {"ever_unresolved_count": len(denominator_primitives)},
        }
    return AttributionLiveEvidence(
        proof,
        primitives,
        _REMOTE_QUERY_ID if primitives else None,
        oracle,
    )


def _transition_oracle(
    transitions: tuple[LateContextTransition, ...],
) -> dict[str, dict[str, int]]:
    oracle: dict[str, dict[str, int]] = {}
    for transition in transitions:
        cohort = _cohort_id(
            transition.producer,
            transition.surface,
            transition.attribution_method,
            transition.prior_reason_code or "none",
        )
        oracle.setdefault(cohort, {"transition_count": 0})["transition_count"] += 1
    return oracle


def _cohort_id(producer: str, surface: str, method: str, prior_reason: str) -> str:
    return hashlib.sha256(
        "\x1f".join(("late-context", producer, surface, method, prior_reason)).encode("utf-8")
    ).hexdigest()[:16]


def _canonical_event_id(
    activity_id: str, version: int, payload_schema_version: int, event_name: str
) -> str:
    return hashlib.sha256(
        "\x1f".join((activity_id, str(version), str(payload_schema_version), event_name)).encode(
            "utf-8"
        )
    ).hexdigest()


def _versions(
    rows: tuple[tuple[object, ...], ...],
    intervals: tuple[tuple[object, ...], ...],
    selected_activity_ids: set[str],
) -> tuple[tuple[ActivityVersion, ...], bool]:
    lifecycle = _lifecycle_index(intervals)
    result: list[ActivityVersion] = []
    authority_missing = False
    observed_activity_ids: set[str] = set()
    for row in rows:
        observed_activity_ids.add(str(row[0]))
        try:
            source_time_ns = _exact_int(row[4])
            version = _exact_int(row[5], positive=True)
            source_time = _source_datetime(source_time_ns)
            producer, surface, session_id = str(row[1]), str(row[2]), str(row[3])
            if (producer, surface) not in _SUPPORTED_PAIRS or not _contained(
                lifecycle.get((producer, session_id), ()), source_time
            ):
                authority_missing = True
                continue
            event_id = str(row[10]) if row[10] is not None else ""
            payload_schema_version = _exact_int(row[11], positive=True)
            event_name = row[12]
            if (
                payload_schema_version != _CANONICAL_PAYLOAD_SCHEMA_VERSION
                or event_name != _CANONICAL_EVENT_NAME
                or event_id
                != _canonical_event_id(
                    str(row[0]), version, payload_schema_version, str(event_name)
                )
            ):
                authority_missing = True
                continue
            result.append(
                ActivityVersion(
                    activity_id=str(row[0]),
                    version=version,
                    event_id=event_id,
                    producer=producer,
                    surface=surface,
                    source_time_ns=source_time_ns,
                    attribution_state=str(row[6]),
                    attribution_method=str(row[7]),
                    reason_code=None if row[8] is None else str(row[8]),
                    project_id=None if row[9] is None else str(row[9]),
                )
            )
        except (TypeError, ValueError, OverflowError, OSError):
            authority_missing = True
    return tuple(result), authority_missing or observed_activity_ids != selected_activity_ids


def _exact_int(value: object, *, positive: bool = False) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or (positive and value <= 0):
        raise ValueError("immutable numeric field is not an exact integer")
    return value


def _lifecycle_index(
    rows: tuple[tuple[object, ...], ...],
) -> dict[tuple[str, str], tuple[tuple[datetime, datetime | None], ...]]:
    grouped: dict[tuple[str, str], list[tuple[datetime, datetime | None]]] = {}
    for producer, session_id, start, end in rows:
        try:
            start_time = _instant(start)
            end_time = None if end is None else _instant(end)
            if end_time is not None and end_time < start_time:
                continue
            grouped.setdefault((str(producer), str(session_id)), []).append((start_time, end_time))
        except (TypeError, ValueError):
            continue
    return {key: tuple(value) for key, value in grouped.items()}


def _contained(
    intervals: tuple[tuple[datetime, datetime | None], ...], source_time: datetime
) -> bool:
    return any(
        start <= source_time and (end is None or source_time < end) for start, end in intervals
    )


def _epoch_ns(instant: datetime) -> int:
    return (
        (instant.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)).days * 86_400_000_000_000
        + (instant.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)).seconds * 1_000_000_000
        + instant.astimezone(UTC).microsecond * 1_000
    )


def _instant(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp is not timezone-aware")
    return parsed


def _missing_tables(connection: sqlite3.Connection) -> tuple[str, ...]:
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    return tuple(sorted(_REQUIRED_TABLES - tables))


def _blocked(request: LiveProofRequest, boundaries: tuple[str, ...]) -> AttributionLiveEvidence:
    reduction = reduce_late_context((), start=request.start, end=request.end)
    proof = build_late_context_proof(
        request.run_id,
        EvidenceProvenance.FRESH_REAL,
        request.source_boundary,
        reduction,
        remote_denominator=None,
    )
    # Preserve schema blockers plus mandatory remote denominator authority.
    proof = replace(
        proof,
        blocked_boundaries=tuple(sorted(set(proof.blocked_boundaries) | set(boundaries))),
    )
    return AttributionLiveEvidence(proof, (), None, {})


def _source_datetime(source_time_ns: int) -> datetime:
    seconds, nanoseconds = divmod(source_time_ns, 1_000_000_000)
    return datetime.fromtimestamp(seconds, UTC).replace(microsecond=nanoseconds // 1_000)

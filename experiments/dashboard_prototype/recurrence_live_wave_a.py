"""Live fail-closed E-Recurrence-0 extraction from canonical activity versions."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Final

from experiments.dashboard_prototype.contracts import EvidenceProvenance, ExperimentResult
from experiments.dashboard_prototype.experiment_live_common import LiveProofRequest
from experiments.dashboard_prototype.recurrence_common import (
    SUPPORTED_SURFACES,
    RecurrenceExperimentId,
    RecurrenceExperimentProof,
    RecurrenceLiveEvidence,
    RecurrencePrimitive,
)
from experiments.dashboard_prototype.recurrence_wave_a import (
    M13Availability,
    M13TaskIdentityState,
    RecurrenceOccurrence,
    RecurrenceProofBoundary,
    build_proof,
    reduce_m16_project_concentration,
)

_ACTIVITY_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "id",
        "producer",
        "producer_surface",
        "source_ended_at_ns",
        "detector_id",
        "operation_kind",
        "target_kind",
        "normalized_target",
        "normalized_failure_class",
    }
)
_VERSION_COLUMNS: Final[frozenset[str]] = frozenset(
    {"activity_id", "version", "attribution_state", "project_identity_id"}
)


def extract(connection: sqlite3.Connection, request: LiveProofRequest) -> RecurrenceLiveEvidence:
    """Extract latest-version M16 primitives and fail closed on unavailable M13 identity."""
    if _has_ambiguous_latest_activity_version(connection, request):
        return _failed_ambiguous_latest_activity_version(request)
    rows = _activity_rows(connection, request)
    occurrences = tuple(
        RecurrenceOccurrence(
            detector=_fingerprint("detector", str(row[0])),
            activity_fingerprint=_fingerprint(
                "activity", *(str(value or "") for value in row[1:5])
            ),
            project_id=_fingerprint("project", str(row[6]))
            if row[5] == "resolved" and row[6] is not None
            else None,
        )
        for row in rows
    )
    m16 = reduce_m16_project_concentration(occurrences)
    observed = {(str(row[7]), str(row[8])) for row in rows}
    m13 = tuple(
        M13Availability(
            producer=producer,
            surface=surface,
            state=(
                M13TaskIdentityState.ABSENT
                if (producer, surface) in observed
                else M13TaskIdentityState.AMBIGUOUS
            ),
            population_count=sum(1 for row in rows if row[7] == producer and row[8] == surface),
        )
        for producer, surface in SUPPORTED_SURFACES
    )
    primitives = _primitives(rows, occurrences, m13, request)
    m16_primitives = tuple(
        primitive for primitive in primitives if primitive.dimensions["policy_identity"] == "m16"
    )
    source_time_ns_bound = all(
        primitive.source_time_ns == int(str(row[9]))
        and primitive.dimensions["timestamp_ns"] == int(str(row[9]))
        for primitive, row in zip(m16_primitives, rows, strict=True)
    )
    proof = build_proof(
        boundary=RecurrenceProofBoundary(
            run_id=request.run_id,
            source_start=request.start,
            source_end=request.end,
            source_boundary=request.source_boundary,
            m16_source_time_ns_bound=source_time_ns_bound,
        ),
        m16=m16,
        m13=m13,
        provenance=EvidenceProvenance.FRESH_REAL,
    )
    return RecurrenceLiveEvidence(proof=proof, primitives=primitives, remote_calculation_id=None)


def _failed_ambiguous_latest_activity_version(
    request: LiveProofRequest,
) -> RecurrenceLiveEvidence:
    """Fail closed when immutable latest activity state has no unique version."""
    proof = RecurrenceExperimentProof(
        experiment_id=RecurrenceExperimentId.WAVE_A,
        run_id=request.run_id,
        result=ExperimentResult.FAILED,
        provenance=EvidenceProvenance.FRESH_REAL,
        source_start=request.start,
        source_end=request.end,
        source_boundary=request.source_boundary,
        metrics={},
        assertions={"contradiction_detected": True},
        evidence_ids=(),
        blocked_boundaries=("ambiguous_latest_activity_version",),
        proposal="reject-ambiguous-latest-activity-version",
    )
    return RecurrenceLiveEvidence(proof=proof, primitives=(), remote_calculation_id=None)


def _primitives(
    rows: tuple[tuple[object, ...], ...],
    occurrences: tuple[RecurrenceOccurrence, ...],
    m13: tuple[M13Availability, ...],
    request: LiveProofRequest,
) -> tuple[RecurrencePrimitive, ...]:
    activity_primitives = tuple(
        RecurrencePrimitive(
            experiment_id=RecurrenceExperimentId.WAVE_A,
            source_time=_source_time_envelope(int(str(row[9]))),
            source_time_ns=int(str(row[9])),
            ordinal=ordinal,
            dimensions={
                "policy_identity": "m16",
                "producer": str(row[7]),
                "surface": str(row[8]),
                "detector_fingerprint": _fingerprint("detector", str(row[0])),
                "normalized_operation": _fingerprint("operation", str(row[1] or "")),
                "allowlisted_normalized_target": _fingerprint(
                    "target", str(row[2] or ""), str(row[3] or "")
                ),
                "error_class": _fingerprint("failure", str(row[4] or "")),
                "stable_identity": occurrence.activity_fingerprint,
                "project_id_or_unresolved_state": occurrence.project_id or "unresolved",
                "capability_state": "resolved" if occurrence.project_id else "unresolved",
                "timestamp_ns": int(str(row[9])),
            },
            measures={"reducer_counts": 1},
        )
        for ordinal, (row, occurrence) in enumerate(zip(rows, occurrences, strict=True))
    )
    offset = len(activity_primitives)
    availability_primitives = tuple(
        RecurrencePrimitive(
            experiment_id=RecurrenceExperimentId.WAVE_A,
            source_time=request.end.astimezone(UTC),
            ordinal=offset + ordinal,
            dimensions={
                "policy_identity": "m13",
                "producer": row.producer,
                "surface": row.surface,
                "capability_state": row.state.value,
                "stable_identity": _fingerprint("population", row.producer, row.surface),
                "timestamp_ns": _datetime_to_ns(request.end),
            },
            measures={"reducer_counts": row.population_count},
        )
        for ordinal, row in enumerate(m13)
    )
    return activity_primitives + availability_primitives


def _has_ambiguous_latest_activity_version(
    connection: sqlite3.Connection, request: LiveProofRequest
) -> bool:
    """Return whether an in-window activity has duplicate globally latest versions."""
    if not _has_required_schema(connection):
        return False
    routes = " OR ".join(
        "(activity.producer = ? AND activity.producer_surface = ?)" for _ in SUPPORTED_SURFACES
    )
    parameters = tuple(item for pair in SUPPORTED_SURFACES for item in pair)
    start_ns = _datetime_to_ns(request.start)
    end_ns = _datetime_to_ns(request.end)
    query = f"""
        SELECT 1
        FROM canonical_activities AS activity
        JOIN canonical_activity_versions AS version
          ON version.activity_id = activity.id
        WHERE ({routes})
          AND activity.source_ended_at_ns > ? AND activity.source_ended_at_ns <= ?
          AND version.version = (
            SELECT MAX(latest.version)
            FROM canonical_activity_versions AS latest
            WHERE latest.activity_id = activity.id
          )
        GROUP BY activity.id
        HAVING COUNT(*) > 1
        LIMIT 1
    """
    return connection.execute(query, (*parameters, start_ns, end_ns)).fetchone() is not None


def _activity_rows(
    connection: sqlite3.Connection, request: LiveProofRequest
) -> tuple[tuple[object, ...], ...]:
    if not _has_required_schema(connection):
        return ()
    routes = " OR ".join(
        "(activity.producer = ? AND activity.producer_surface = ?)" for _ in SUPPORTED_SURFACES
    )
    parameters = tuple(item for pair in SUPPORTED_SURFACES for item in pair)
    start_ns = _datetime_to_ns(request.start)
    end_ns = _datetime_to_ns(request.end)
    query = f"""
        WITH latest AS (
          SELECT version.activity_id, version.version, version.attribution_state,
                 version.project_identity_id
          FROM canonical_activity_versions AS version
          WHERE version.version = (
            SELECT MAX(candidate.version)
            FROM canonical_activity_versions AS candidate
            WHERE candidate.activity_id = version.activity_id
          )
        )
        SELECT activity.detector_id, activity.operation_kind, activity.target_kind,
               activity.normalized_target, activity.normalized_failure_class,
               latest.attribution_state, latest.project_identity_id, activity.producer,
               activity.producer_surface, activity.source_ended_at_ns
        FROM canonical_activities AS activity JOIN latest
          ON latest.activity_id = activity.id
        WHERE ({routes})
          AND activity.source_ended_at_ns > ? AND activity.source_ended_at_ns <= ?
        ORDER BY activity.source_ended_at_ns, activity.id
    """
    return tuple(connection.execute(query, (*parameters, start_ns, end_ns)))


def _has_required_schema(connection: sqlite3.Connection) -> bool:
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if not {"canonical_activities", "canonical_activity_versions"} <= tables:
        return False
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(canonical_activities)")}
    versions = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(canonical_activity_versions)")
    }
    return columns >= _ACTIVITY_COLUMNS and versions >= _VERSION_COLUMNS


def _datetime_to_ns(value: datetime) -> int:
    """Return an exact UTC epoch-nanosecond boundary for an aware datetime."""
    utc_value = value.astimezone(UTC)
    delta = utc_value - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


def _source_time_envelope(source_time_ns: int) -> datetime:
    """Represent exact source nanoseconds in the datetime envelope without float loss."""
    seconds, nanoseconds = divmod(source_time_ns, 1_000_000_000)
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
        seconds=seconds, microseconds=nanoseconds // 1_000
    )


def _fingerprint(kind: str, *values: str) -> str:
    digest = hashlib.sha256("\x1f".join(values).encode("utf-8")).hexdigest()
    return f"{kind}:{digest}"

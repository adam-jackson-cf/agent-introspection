"""Fresh-real E-Task-0 extraction from canonical activity route manifests."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC
from typing import Final

from experiments.dashboard_prototype.contracts import EvidenceProvenance
from experiments.dashboard_prototype.experiment_live_common import (
    LiveProofRequest,
    RemoteCalculationPrimitive,
)
from experiments.dashboard_prototype.task_availability import (
    DETECTOR_MEASURES,
    PROJECT_ACTIVITY_MEASURE,
    TaskAvailabilityClassification,
    TaskRouteState,
    build_proof,
    classification_counts,
    current_classifications,
    validate_classifications,
)
from experiments.dashboard_prototype.task_common import (
    SUPPORTED_SURFACES,
    TaskExperimentId,
    TaskLiveEvidence,
)

_ACTIVITY_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "id",
        "producer",
        "producer_surface",
        "correlation_id",
        "source_started_at_ns",
        "source_ended_at_ns",
        "detector_id",
        "detector_version",
        "normalization_version",
        "source_membership_hash",
        "operation_kind",
        "target_kind",
        "normalized_target",
        "normalized_failure_class",
    }
)
_VERSION_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "activity_id",
        "version",
        "attribution_state",
        "project_identity_id",
        "attribution_method",
    }
)


def extract(connection: sqlite3.Connection, request: LiveProofRequest) -> TaskLiveEvidence:
    """Classify detector-specific fresh routes without exposing source identities."""
    populations = _route_populations(connection, request)
    classifications = _live_classifications(connection, populations)
    proof = build_proof(
        request.run_id,
        classifications,
        source_boundary=request.source_boundary,
        provenance=EvidenceProvenance.FRESH_REAL,
        route_population=sum(population[0] for population in populations.values()),
    )
    counts = classification_counts(classifications)
    primitives = tuple(
        RemoteCalculationPrimitive(
            experiment_id=TaskExperimentId.AVAILABILITY,
            source_time=request.end.astimezone(UTC),
            ordinal=ordinal,
            dimensions={
                "producer": row.producer,
                "surface": row.surface,
                "measure": row.measure,
                "route_state": row.state.value,
                "capability": row.capability,
                "time_domain": row.time_domain,
                "population": row.population,
                "redaction_boundary": row.redaction_boundary,
            },
            measures={
                "route_count": 1,
                "activity_population": _population(populations, row)[0],
                "resolved_count": _population(populations, row)[1],
                "unresolved_count": _population(populations, row)[2],
            },
        )
        for ordinal, row in enumerate(classifications)
    )
    return TaskLiveEvidence(
        proof=proof,
        primitives=primitives,
        remote_query_id=None,
        remote_oracle={state.value: {"route_count": count} for state, count in counts.items()},
    )


def _key(row: TaskAvailabilityClassification) -> tuple[str, str, str]:
    return row.producer, row.surface, row.measure


def _population(
    populations: dict[tuple[str, str, str], tuple[int, int, int]],
    row: TaskAvailabilityClassification,
) -> tuple[int, int, int]:
    return populations.get(_key(row), (0, 0, 0))


def _live_classifications(
    connection: sqlite3.Connection,
    populations: dict[tuple[str, str, str], tuple[int, int, int]],
) -> tuple[TaskAvailabilityClassification, ...]:
    schema_ready = _has_required_schema(connection)
    rows: Iterable[TaskAvailabilityClassification] = (
        _reclassify(row, schema_ready, populations) for row in current_classifications()
    )
    return validate_classifications(rows)


def _reclassify(
    row: TaskAvailabilityClassification,
    schema_ready: bool,
    populations: dict[tuple[str, str, str], tuple[int, int, int]],
) -> TaskAvailabilityClassification:
    if row.state is TaskRouteState.UNSUPPORTED or row.measure in {"M2", "M3", "M4"}:
        return row
    if not schema_ready:
        return row
    state = TaskRouteState.AUTHORITATIVE if _key(row) in populations else TaskRouteState.AMBIGUOUS
    return TaskAvailabilityClassification(
        row.producer,
        row.surface,
        row.measure,
        state,
        row.capability,
        row.time_domain,
        row.population,
        row.redaction_boundary,
    )


def _has_required_schema(connection: sqlite3.Connection) -> bool:
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if not {"canonical_activities", "canonical_activity_versions"}.issubset(tables):
        return False
    activity_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(canonical_activities)")
    }
    version_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(canonical_activity_versions)")
    }
    return activity_columns >= _ACTIVITY_COLUMNS and version_columns >= _VERSION_COLUMNS


def _route_populations(
    connection: sqlite3.Connection, request: LiveProofRequest
) -> dict[tuple[str, str, str], tuple[int, int, int]]:
    if not _has_required_schema(connection):
        return {}
    start_ns = int(request.start.astimezone(UTC).timestamp() * 1_000_000_000)
    end_ns = int(request.end.astimezone(UTC).timestamp() * 1_000_000_000)
    routes = " OR ".join(
        "(activity.producer = ? AND activity.producer_surface = ?)" for _ in SUPPORTED_SURFACES
    )
    parameters = tuple(item for pair in SUPPORTED_SURFACES for item in pair)
    detector_rows = connection.execute(
        f"""
        WITH latest AS (
          SELECT activity_id, version, attribution_state, project_identity_id,
                 row_number() OVER (PARTITION BY activity_id ORDER BY version DESC) AS rank
          FROM canonical_activity_versions
        )
        SELECT activity.producer, activity.producer_surface, activity.detector_id, count(*)
        FROM canonical_activities AS activity JOIN latest
          ON latest.activity_id = activity.id AND latest.rank = 1
        WHERE ({routes}) AND activity.source_ended_at_ns > ? AND activity.source_ended_at_ns <= ?
        GROUP BY activity.producer, activity.producer_surface, activity.detector_id
        """,
        (*parameters, start_ns, end_ns),
    )
    populations: dict[tuple[str, str, str], tuple[int, int, int]] = {}
    detector_measures = {detector: measure for measure, detector in DETECTOR_MEASURES.items()}
    for producer, surface, detector, count in detector_rows:
        measure = detector_measures.get(str(detector))
        if measure is not None:
            populations[(str(producer), str(surface), measure)] = (int(count), 0, 0)
    project_rows = connection.execute(
        f"""
        WITH latest AS (
          SELECT activity_id, version, attribution_state, project_identity_id,
                 row_number() OVER (PARTITION BY activity_id ORDER BY version DESC) AS rank
          FROM canonical_activity_versions
        )
        SELECT activity.producer, activity.producer_surface, count(*),
               sum(CASE WHEN latest.attribution_state = 'resolved'
                             AND latest.project_identity_id IS NOT NULL THEN 1 ELSE 0 END),
               sum(CASE WHEN latest.attribution_state = 'unresolved' THEN 1 ELSE 0 END)
        FROM canonical_activities AS activity JOIN latest
          ON latest.activity_id = activity.id AND latest.rank = 1
        WHERE ({routes}) AND activity.source_ended_at_ns > ? AND activity.source_ended_at_ns <= ?
        GROUP BY activity.producer, activity.producer_surface
        """,
        (*parameters, start_ns, end_ns),
    )
    for producer, surface, total, resolved, unresolved in project_rows:
        populations[(str(producer), str(surface), PROJECT_ACTIVITY_MEASURE)] = (
            int(total),
            int(resolved),
            int(unresolved),
        )
    return populations

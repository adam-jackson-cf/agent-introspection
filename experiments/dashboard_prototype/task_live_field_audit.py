"""Live E-Task-1 classification from canonical activities and latest versions."""

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
from experiments.dashboard_prototype.task_common import TaskExperimentId, TaskLiveEvidence
from experiments.dashboard_prototype.task_field_audit import (
    PRODUCER_SURFACES,
    FieldAuthorityState,
    TaskFieldClassification,
    build_proof,
    classification_counts,
    current_classifications,
    validate_classifications,
)

_ACTIVITY_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "id",
        "producer",
        "producer_surface",
        "correlation_id",
        "source_started_at_ns",
        "source_ended_at_ns",
        "source_membership_hash",
        "source_membership_json",
        "operation_kind",
        "target_kind",
        "normalized_target",
        "normalized_failure_class",
    }
)
_VERSION_COLUMNS: Final[frozenset[str]] = frozenset(
    {"activity_id", "version", "attribution_state", "project_identity_id"}
)
_COMMON_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "producer",
        "surface",
        "native_session_id",
        "source_session_relationship",
        "source_timestamp",
        "normalized_operation",
        "project_identity_state",
    }
)


def extract(connection: sqlite3.Connection, request: LiveProofRequest) -> TaskLiveEvidence:
    """Classify live authority without reading task, call, target, or outcome values."""
    classifications = _live_classifications(connection, request)
    proof = build_proof(
        request.run_id,
        classifications,
        source_boundary=request.source_boundary,
        provenance=EvidenceProvenance.FRESH_REAL,
    )
    primitives = tuple(
        RemoteCalculationPrimitive(
            experiment_id=TaskExperimentId.FIELD_AUDIT,
            source_time=request.end.astimezone(UTC),
            ordinal=ordinal,
            dimensions={
                "producer": row.producer,
                "surface": row.surface,
                "field": row.field,
                "authority_state": row.state.value,
            },
            measures={"classification_count": 1},
        )
        for ordinal, row in enumerate(classifications)
    )
    counts = classification_counts(classifications)
    return TaskLiveEvidence(
        proof=proof,
        primitives=primitives,
        remote_query_id=None,
        remote_oracle={
            state.value: {"classification_count": count} for state, count in counts.items()
        },
    )


def _live_classifications(
    connection: sqlite3.Connection, request: LiveProofRequest
) -> tuple[TaskFieldClassification, ...]:
    schema_supported = _schema_supported(connection)
    observed = _observed_producer_surfaces(connection, request) if schema_supported else frozenset()
    return validate_classifications(
        _reclassify(row, schema_supported, observed) for row in current_classifications()
    )


def _reclassify(
    row: TaskFieldClassification,
    schema_supported: bool,
    observed: frozenset[tuple[str, str]],
) -> TaskFieldClassification:
    if not schema_supported:
        return TaskFieldClassification(
            row.producer, row.surface, row.field, FieldAuthorityState.ABSENT
        )
    if row.field not in _COMMON_FIELDS:
        return row
    if (row.producer, row.surface) not in observed:
        # An empty in-window cohort establishes neither zero population nor unavailable authority.
        state = FieldAuthorityState.AMBIGUOUS
    else:
        state = FieldAuthorityState.AUTHORITATIVE
    return TaskFieldClassification(row.producer, row.surface, row.field, state)


def _schema_supported(connection: sqlite3.Connection) -> bool:
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if not {"canonical_activities", "canonical_activity_versions"} <= tables:
        return False
    activity_columns = frozenset(
        str(row[1]) for row in connection.execute("PRAGMA table_info(canonical_activities)")
    )
    version_columns = frozenset(
        str(row[1]) for row in connection.execute("PRAGMA table_info(canonical_activity_versions)")
    )
    return activity_columns >= _ACTIVITY_COLUMNS and version_columns >= _VERSION_COLUMNS


def _observed_producer_surfaces(
    connection: sqlite3.Connection, request: LiveProofRequest
) -> frozenset[tuple[str, str]]:
    pairs = tuple(PRODUCER_SURFACES)
    pair_filter = " OR ".join(
        "(activity.producer = ? AND activity.producer_surface = ?)" for _ in pairs
    )
    rows: Iterable[tuple[str, str]] = connection.execute(
        f"""
        WITH latest_versions AS (
            SELECT activity_id, MAX(version) AS version
            FROM canonical_activity_versions
            GROUP BY activity_id
        )
        SELECT DISTINCT activity.producer, activity.producer_surface
        FROM canonical_activities AS activity
        JOIN latest_versions AS latest ON latest.activity_id = activity.id
        JOIN canonical_activity_versions AS version
          ON version.activity_id = latest.activity_id AND version.version = latest.version
        WHERE ({pair_filter})
          AND activity.source_ended_at_ns > ?
          AND activity.source_ended_at_ns <= ?
          AND activity.source_membership_hash <> ''
          AND activity.source_membership_json <> ''
          AND version.attribution_state IN ('resolved', 'unresolved')
        """,
        (
            *(item for pair in pairs for item in pair),
            int(request.start.astimezone(UTC).timestamp() * 1_000_000_000),
            int(request.end.astimezone(UTC).timestamp() * 1_000_000_000),
        ),
    )
    return frozenset((str(producer), str(surface)) for producer, surface in rows)

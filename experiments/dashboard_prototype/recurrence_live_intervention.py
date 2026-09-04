"""Fail-closed live audit for E-Recurrence-4 intervention evidence."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
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

_AUDITED_POPULATIONS: Final[tuple[str, ...]] = (
    "canonical_activities",
    "findings",
    "proposals",
    "proposal_events",
    "proposal_drafts",
    "semantic_classifications",
)
_REQUIRED_PROJECTIONS: Final[tuple[str, ...]] = (
    "canonical_practices",
    "intervention_applications",
    "recurrence_projections",
)
_REQUIRED_CANONICAL: Final[dict[str, frozenset[str]]] = {
    "canonical_activities": frozenset({"id", "producer", "producer_surface", "source_ended_at_ns"}),
    "canonical_activity_versions": frozenset({"activity_id", "version"}),
}
_QUERY_ID: Final[str] = "recurrence-intervention-audit-v1"


_UTC_EPOCH: Final[datetime] = datetime(1970, 1, 1, tzinfo=UTC)


def _utc_epoch_ns(value: datetime) -> int:
    delta = value.astimezone(UTC) - _UTC_EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


def extract(connection: sqlite3.Connection, request: LiveProofRequest) -> RecurrenceLiveEvidence:
    """Audit safe schema/state/count primitives; never infer authority from emptiness."""
    tables = _table_columns(connection)
    populations = _AUDITED_POPULATIONS + _REQUIRED_PROJECTIONS
    primitives = tuple(
        RecurrencePrimitive(
            experiment_id=RecurrenceExperimentId.INTERVENTION_AUDIT,
            source_time=request.end.astimezone(UTC),
            ordinal=ordinal,
            dimensions={
                "stable_identity": population,
                "capability_state": _audit_state(tables, population),
            },
            measures={"reducer_counts": _bounded_count(connection, tables, population, request)},
        )
        for ordinal, population in enumerate(populations)
    )
    missing = tuple(
        name
        for name, columns in _REQUIRED_CANONICAL.items()
        if not columns <= tables.get(name, frozenset())
    )
    projection_gaps = tuple(
        name
        for name in _REQUIRED_PROJECTIONS
        if not _projection_is_authoritative(connection, tables, name, request)
    )
    blocked = tuple(dict.fromkeys(("missing_durable_authority", *missing, *projection_gaps)))
    proof = RecurrenceExperimentProof(
        experiment_id=RecurrenceExperimentId.INTERVENTION_AUDIT,
        run_id=request.run_id,
        result=ExperimentResult.BLOCKED,
        provenance=EvidenceProvenance.FRESH_REAL,
        source_start=request.start,
        source_end=request.end,
        source_boundary=request.source_boundary,
        metrics={
            "audited_population_count": len(_AUDITED_POPULATIONS),
            "finding_row_count": _bounded_count(connection, tables, "findings", request),
            "required_projection_count": len(_REQUIRED_PROJECTIONS),
            "missing_schema_count": len(missing),
            "projection_gap_count": len(projection_gaps),
        },
        assertions={
            "workflow_is_full_projection": False,
            "canonical_practice_available": "canonical_practices" not in projection_gaps,
            "application_projection_available": "intervention_applications" not in projection_gaps,
            "recurrence_projection_available": "recurrence_projections" not in projection_gaps,
        },
        evidence_ids=(_QUERY_ID,),
        blocked_boundaries=blocked,
        proposal=(
            "Audit safe intervention schema and authority without inferring empty populations."
        ),
    )
    return RecurrenceLiveEvidence(proof, primitives, None)


def _table_columns(connection: sqlite3.Connection) -> dict[str, frozenset[str]]:
    names = tuple(
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    )
    return {
        name: frozenset(str(row[1]) for row in connection.execute(f'PRAGMA table_info("{name}")'))
        for name in names
    }


def _audit_state(tables: dict[str, frozenset[str]], population: str) -> str:
    if population not in tables:
        return "missing_schema"
    timestamp_column = _bounded_timestamp_column(population)
    if timestamp_column is None or timestamp_column not in tables[population]:
        return "missing_time_schema"
    return "auditable"


def _projection_is_authoritative(
    connection: sqlite3.Connection,
    tables: dict[str, frozenset[str]],
    population: str,
    request: LiveProofRequest,
) -> bool:
    """Presence or rows alone cannot establish authoritative projection coverage."""
    del connection, tables, population, request
    return False


def _bounded_timestamp_column(population: str) -> str | None:
    if population == "canonical_activities":
        return "source_ended_at_ns"
    if population == "findings":
        return "last_seen_ns"
    return None


def _bounded_count(
    connection: sqlite3.Connection,
    tables: dict[str, frozenset[str]],
    population: str,
    request: LiveProofRequest,
) -> int:
    if population == "canonical_activities":
        return _latest_canonical_count(connection, tables, request)
    timestamp_column = _bounded_timestamp_column(population)
    columns = tables.get(population, frozenset())
    if timestamp_column is None or timestamp_column not in columns:
        return 0
    start_ns = _utc_epoch_ns(request.start)
    end_ns = _utc_epoch_ns(request.end)
    row = connection.execute(
        f'SELECT COUNT(*) FROM "{population}" '
        f"WHERE {timestamp_column} > ? AND {timestamp_column} <= ?",
        (start_ns, end_ns),
    ).fetchone()
    return int(row[0]) if row else 0


def _latest_canonical_count(
    connection: sqlite3.Connection,
    tables: dict[str, frozenset[str]],
    request: LiveProofRequest,
) -> int:
    if not (
        _REQUIRED_CANONICAL["canonical_activities"]
        <= tables.get("canonical_activities", frozenset())
        and _REQUIRED_CANONICAL["canonical_activity_versions"]
        <= tables.get("canonical_activity_versions", frozenset())
    ):
        return 0
    start_ns = _utc_epoch_ns(request.start)
    end_ns = _utc_epoch_ns(request.end)
    pairs = tuple(SUPPORTED_SURFACES)
    pair_filter = " OR ".join(
        "(activity.producer = ? AND activity.producer_surface = ?)" for _ in pairs
    )
    row = connection.execute(
        f"""
        WITH latest_versions AS (
            SELECT activity_id, MAX(version) AS version
            FROM canonical_activity_versions
            GROUP BY activity_id
        )
        SELECT COUNT(*)
        FROM canonical_activities AS activity
        JOIN latest_versions AS latest ON latest.activity_id = activity.id
        WHERE ({pair_filter})
          AND activity.source_ended_at_ns > ?
          AND activity.source_ended_at_ns <= ?
        """,
        (
            *(item for pair in pairs for item in pair),
            start_ns,
            end_ns,
        ),
    ).fetchone()
    return int(row[0]) if row else 0

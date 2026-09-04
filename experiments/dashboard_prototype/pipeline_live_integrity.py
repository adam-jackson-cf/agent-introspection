"""Bounded SQLite adapter for the redacted E-Pipeline-4 integrity proof."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import datetime
from typing import Final

from experiments.dashboard_prototype.contracts import EvidenceProvenance, ExperimentResult
from experiments.dashboard_prototype.pipeline_common import PipelineExperimentId
from experiments.dashboard_prototype.pipeline_integrity import (
    IntegrityInvariant,
    IntegrityObservation,
    build_integrity_proof,
    reduce_integrity_incidents,
)
from experiments.dashboard_prototype.pipeline_live_common import (
    LiveExperimentEvidence,
    LiveProofRequest,
    RemoteCalculationPrimitive,
)

_SAFE_TOKEN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,47}\Z")
_REQUIRED_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "producer",
        "producer_surface",
        "lifecycle_event",
        "occurred_at",
        "reason_code",
        "source_adapter",
        "id",
    }
)
_REASON_INVARIANTS: Final[dict[str, IntegrityInvariant]] = {
    "missing_correlation_id": IntegrityInvariant.CANONICAL_LIFECYCLE_CONTEXT_REJECTION,
    "missing_workspace": IntegrityInvariant.CANONICAL_LIFECYCLE_CONTEXT_REJECTION,
    "invalid_workspace": IntegrityInvariant.CANONICAL_LIFECYCLE_CONTEXT_REJECTION,
    "non_git_workspace": IntegrityInvariant.CANONICAL_LIFECYCLE_CONTEXT_REJECTION,
    "git_resolution_failed": IntegrityInvariant.CANONICAL_LIFECYCLE_CONTEXT_REJECTION,
    "conflicting_correlation_id": IntegrityInvariant.CONFLICTING_IDENTITY,
    "duplicate_conflict": IntegrityInvariant.DETERMINISTIC_ID_CONFLICT,
    "invalid_timestamp": IntegrityInvariant.VERSION_GAP,
    "invalid_transition": IntegrityInvariant.VERSION_GAP,
    "out_of_order_event": IntegrityInvariant.VERSION_GAP,
}
_REMOTE_QUERY_ID: Final[str] = "p11-6b2487cf6f5f395b3409d2de"


def extract(
    connection: sqlite3.Connection,
    request: LiveProofRequest,
    *,
    expected_remote_counts: Mapping[str, int] | None = None,
) -> LiveExperimentEvidence:
    """Extract redacted canonical rejections for one exact fresh-real window."""
    rows, source_boundary = _load_observations(connection, request)
    reduction = reduce_integrity_incidents(rows, start=request.start, end=request.end)
    proof = build_integrity_proof(
        reduction,
        run_id=request.run_id,
        provenance=EvidenceProvenance.FRESH_REAL,
        source_boundary=source_boundary,
        expected_remote_counts=expected_remote_counts,
    )
    proof = replace(
        proof,
        result=(
            ExperimentResult.BLOCKED if proof.result is ExperimentResult.PROVEN else proof.result
        ),
        assertions={
            **proof.assertions,
            "durable_integrity_failure_population_authoritative": False,
        },
        blocked_boundaries=tuple(
            sorted(set(proof.blocked_boundaries) | {"durable-integrity-failure population"})
        ),
    )
    return LiveExperimentEvidence(
        proof=proof,
        primitives=_primitives(rows, reduction.withheld_cohorts, proof.experiment_id, request.end),
        remote_query_id=_REMOTE_QUERY_ID,
    )


def _load_observations(
    connection: sqlite3.Connection, request: LiveProofRequest
) -> tuple[tuple[IntegrityObservation, ...], str | None]:
    if not _has_required_schema(connection):
        return (), None
    records = connection.execute(
        """
        SELECT producer, producer_surface, lifecycle_event, occurred_at, reason_code,
               source_adapter, id
        FROM canonical_rejections
        WHERE producer <> ? AND occurred_at > ? AND occurred_at <= ?
        ORDER BY occurred_at, id
        """,
        ("claude-code", request.start.isoformat(), request.end.isoformat()),
    )
    return (
        tuple(_observation(record) for record in records),
        request.source_boundary,
    )


def _has_required_schema(connection: sqlite3.Connection) -> bool:
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        ("canonical_rejections",),
    ).fetchone()
    if table is None:
        return False
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(canonical_rejections)")}
    return columns >= _REQUIRED_COLUMNS


def _observation(record: tuple[str, str, str, str, str, str, str]) -> IntegrityObservation:
    producer, surface, lifecycle_event, occurred_at, reason, source_adapter, durable_id = record
    invariant = _REASON_INVARIANTS.get(reason)
    if invariant is None:
        raise ValueError("unregistered canonical rejection reason")
    if not _SAFE_TOKEN.fullmatch(source_adapter):
        raise ValueError("canonical rejection source adapter is unsafe")
    if not _SAFE_TOKEN.fullmatch(producer) or not _SAFE_TOKEN.fullmatch(lifecycle_event):
        raise ValueError("canonical rejection contains unsafe fixed fields")
    return IntegrityObservation(
        invariant=invariant,
        event_or_projection=lifecycle_event,
        source_time=_parse_occurred_at(occurred_at),
        producer=producer,
        runtime=source_adapter,
        cohort_short_identity=_short_identity("coh", producer, surface),
        affected_short_identity=_short_identity("rej", durable_id),
    )


def _parse_occurred_at(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("canonical rejection occurred_at must be timezone-aware")
    return parsed


def _short_identity(prefix: str, *values: str) -> str:
    return prefix + "-" + hashlib.sha256("\x1f".join(values).encode()).hexdigest()[:24]


def _primitives(
    rows: Iterable[IntegrityObservation],
    withheld_cohorts: tuple[str, ...],
    experiment_id: PipelineExperimentId,
    end: datetime,
) -> tuple[RemoteCalculationPrimitive, ...]:
    withheld = frozenset(withheld_cohorts)
    selected = sorted(
        set(rows),
        key=lambda row: (
            row.source_time,
            _short_identity("obs", *_observation_fields(row)),
        ),
    )
    if not selected:
        return (
            RemoteCalculationPrimitive(
                experiment_id=experiment_id,
                source_time=end,
                ordinal=0,
                dimensions={"metric": "zero_population_marker", "withheld": "false"},
                measures={"incident_count": 0},
            ),
        )
    return tuple(
        RemoteCalculationPrimitive(
            experiment_id=experiment_id,
            source_time=row.source_time,
            ordinal=ordinal,
            dimensions={
                "metric": _metric_name(row),
                "withheld": "true" if row.cohort_short_identity in withheld else "false",
            },
            measures={"incident_count": 1},
        )
        for ordinal, row in enumerate(selected)
    )


def _observation_fields(row: IntegrityObservation) -> tuple[str, ...]:
    return (
        row.invariant.value,
        row.event_or_projection,
        row.source_time.isoformat(),
        row.producer,
        row.runtime,
        row.affected_short_identity,
    )


def _metric_name(row: IntegrityObservation) -> str:
    return f"p11.{row.invariant.value}.{row.producer}.{row.runtime}"

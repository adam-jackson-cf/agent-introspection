"""Live fail-closed E-Recurrence-2 prerequisite audit."""

from __future__ import annotations

import sqlite3
from datetime import UTC
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

# Table names are not authority. These exact durable columns are a minimum schema
# boundary; no current projection supplies all of them, so extraction stays blocked.
_PHASE4_COLUMNS: Final[dict[str, frozenset[str]]] = {
    "task_operation_evidence": frozenset(
        {
            "producer",
            "attempt_hash",
            "canonical_task_id",
            "source_order",
            "operation_fingerprint",
            "target_fingerprint",
            "task_class",
            "occurred_at",
            "authority_evidence_id",
        }
    ),
    "task_terminal_evidence": frozenset(
        {"producer", "attempt_hash", "terminal_outcome", "occurred_at", "authority_evidence_id"}
    ),
    "task_registry_versions": frozenset(
        {
            "sequence_fingerprint",
            "sequence_version",
            "owner_state",
            "owner_version",
            "authority_evidence_id",
        }
    ),
}


_PREREQUISITE_AUDIT_KINDS: Final[tuple[str, ...]] = (
    "ordered_operation_count",
    "explicit_terminal_count",
    "task_class_count",
    "registry_authority_count",
)


def extract(connection: sqlite3.Connection, request: LiveProofRequest) -> RecurrenceLiveEvidence:
    """Emit per-producer unavailable prerequisites; never derive a sequence from aggregates."""
    authority_available = _authoritative_phase4_boundary(connection)
    state = "authority_unavailable" if not authority_available else "authority_not_extracted"
    primitives = tuple(
        RecurrencePrimitive(
            experiment_id=RecurrenceExperimentId.SUCCESSFUL_PRACTICE,
            source_time=request.end.astimezone(UTC),
            ordinal=ordinal,
            dimensions={
                "producer": producer,
                "surface": surface,
                "capability_state": state,
                "stable_identity": count_kind,
            },
            measures={"reducer_counts": 1},
        )
        for ordinal, (producer, surface, count_kind) in enumerate(
            (
                (producer, surface, count_kind)
                for producer, surface in SUPPORTED_SURFACES
                for count_kind in _PREREQUISITE_AUDIT_KINDS
            )
        )
    )
    proof = RecurrenceExperimentProof(
        experiment_id=RecurrenceExperimentId.SUCCESSFUL_PRACTICE,
        run_id=request.run_id,
        result=ExperimentResult.BLOCKED,
        provenance=EvidenceProvenance.FRESH_REAL,
        source_start=request.start,
        source_end=request.end,
        source_boundary=request.source_boundary,
        metrics={"producer_count": len(SUPPORTED_SURFACES)},
        assertions={
            "canonical_aggregate_not_sequence_authority": True,
            "terminal_success_not_inferred": True,
            "remote_calculation_reconciled": False,
        },
        evidence_ids=(),
        blocked_boundaries=("missing_durable_authority",),
        proposal="Await verified Phase4 ordered-operation, terminal, task, and registry evidence.",
    )
    return RecurrenceLiveEvidence(proof=proof, primitives=primitives, remote_calculation_id=None)


def _authoritative_phase4_boundary(connection: sqlite3.Connection) -> bool:
    """Require every authoritative table and field, never merely a table name."""
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if not tables >= _PHASE4_COLUMNS.keys():
        return False
    return all(
        {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")} >= required
        for table, required in _PHASE4_COLUMNS.items()
    )

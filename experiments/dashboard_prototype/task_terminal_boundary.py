"""E-Task-3 fail-closed audit of terminal task-outcome authority."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from experiments.dashboard_prototype.contracts import EvidenceProvenance, ExperimentResult
from experiments.dashboard_prototype.task_common import (
    SUPPORTED_SURFACES,
    TaskExperimentId,
    TaskExperimentProof,
)


class TerminalAuthorityState(StrEnum):
    """The only E3 authority state: installed sources cannot establish an outcome."""

    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class TerminalBoundaryAudit:
    """Privacy-safe producer authority classification, never a task candidate."""

    producer: str
    surface: str
    state: TerminalAuthorityState
    missing_boundary: str

    def __post_init__(self) -> None:
        if (self.producer, self.surface) not in SUPPORTED_SURFACES:
            raise ValueError("terminal audit producer and surface must be canonical")
        if self.state is not TerminalAuthorityState.BLOCKED:
            raise ValueError("terminal task outcome authority must fail closed")
        if not self.missing_boundary:
            raise ValueError("terminal audit requires an explicit missing boundary")


_MISSING_BOUNDARIES: Final[dict[str, str]] = {
    producer: (
        f"{producer} lacks a separate task identity and authoritative terminal "
        "task outcome hook or telemetry boundary"
    )
    for producer, _ in SUPPORTED_SURFACES
}


def current_audits() -> tuple[TerminalBoundaryAudit, ...]:
    """Return one typed blocked audit for every supported installed producer."""
    return tuple(
        TerminalBoundaryAudit(
            producer=producer,
            surface=surface,
            state=TerminalAuthorityState.BLOCKED,
            missing_boundary=_MISSING_BOUNDARIES[producer],
        )
        for producer, surface in SUPPORTED_SURFACES
    )


def validate_audits(audits: Iterable[TerminalBoundaryAudit]) -> tuple[TerminalBoundaryAudit, ...]:
    """Require exactly one blocked boundary for each canonical producer surface."""
    rows = tuple(audits)
    expected = set(SUPPORTED_SURFACES)
    by_surface: dict[tuple[str, str], TerminalBoundaryAudit] = {}
    for row in rows:
        key = (row.producer, row.surface)
        if key in by_surface:
            raise ValueError("terminal audit is duplicated or conflicting")
        by_surface[key] = row
    if set(by_surface) != expected:
        raise ValueError("terminal audit omits or adds a producer surface")
    if any(row.state is not TerminalAuthorityState.BLOCKED for row in rows):
        raise ValueError("terminal authority cannot be inferred")
    return rows


def build_proof(
    run_id: str,
    audits: Iterable[TerminalBoundaryAudit],
    *,
    source_boundary: str = "installed-producer-terminal-authority",
    provenance: EvidenceProvenance = EvidenceProvenance.RETAINED,
    source_authority_present: bool = False,
) -> TaskExperimentProof:
    """Build E3's producer-complete typed Blocked proof with no outcome candidates."""
    rows = validate_audits(audits)
    return TaskExperimentProof(
        experiment_id=TaskExperimentId.TERMINAL_BOUNDARY,
        run_id=run_id,
        result=ExperimentResult.BLOCKED,
        provenance=provenance,
        source_boundary=source_boundary,
        metrics={
            "producer_population": len(rows),
            "blocked_producer_population": len(rows),
            "terminal_candidate_population": 0,
            "source_authority_present": int(source_authority_present),
        },
        assertions={
            "complete_three_producer_audit": len(rows) == len(SUPPORTED_SURFACES),
            "all_terminal_authority_blocked": all(
                row.state is TerminalAuthorityState.BLOCKED for row in rows
            ),
            "no_terminal_task_outcome_inferred": True,
            "context_source_authority_present": source_authority_present,
        },
        evidence_ids=(),
        blocked_boundaries=tuple(row.missing_boundary for row in rows),
        proposal=(
            "separate task identity plus authoritative terminal task outcome hook "
            "or telemetry boundary"
        ),
    )

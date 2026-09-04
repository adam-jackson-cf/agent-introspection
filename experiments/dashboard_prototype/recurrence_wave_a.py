"""Pure E-Recurrence-0 reducers for project concentration and task authority."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final

from experiments.dashboard_prototype.contracts import (
    EvidenceProvenance,
    ExperimentResult,
)
from experiments.dashboard_prototype.recurrence_common import (
    RecurrenceExperimentId,
    RecurrenceExperimentProof,
)

_FINGERPRINT = re.compile(r"[a-z][a-z0-9_-]*:[0-9a-f]{64}\Z", re.ASCII)
_TOKEN = re.compile(r"[a-z][a-z0-9_.:-]{0,127}\Z", re.ASCII)
_MISSING_DURABLE_AUTHORITY: Final[str] = "missing_durable_authority"


class M13TaskIdentityState(StrEnum):
    """Whether a route exposes durable canonical task identity."""

    ABSENT = "absent"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class RecurrenceOccurrence:
    """One already-version-selected, privacy-safe activity occurrence."""

    detector: str
    activity_fingerprint: str
    project_id: str | None

    def __post_init__(self) -> None:
        _require_token(self.detector, "detector")
        _require_fingerprint(self.activity_fingerprint)
        if self.project_id is not None:
            _require_fingerprint(self.project_id)


@dataclass(frozen=True, slots=True)
class M16ProjectConcentration:
    """Conserved project concentration for one detector/fingerprint population."""

    detector: str
    activity_fingerprint: str
    total_count: int
    attributed_count: int
    unresolved_count: int
    top_project_id: str | None
    top_project_count: int
    top_project_percentage: float | None


@dataclass(frozen=True, slots=True)
class M13Availability:
    """Per-route durable canonical-task authority availability."""

    producer: str
    surface: str
    state: M13TaskIdentityState
    population_count: int


@dataclass(frozen=True, slots=True)
class RecurrenceProofBoundary:
    """Immutable source boundary for one recurrence proof."""

    run_id: str
    source_start: datetime
    source_end: datetime
    source_boundary: str
    m16_source_time_ns_bound: bool = True


def reduce_m16_project_concentration(
    occurrences: Iterable[RecurrenceOccurrence],
) -> tuple[M16ProjectConcentration, ...]:
    """Conserve each detector/fingerprint population while excluding unresolved from M16."""
    grouped: dict[tuple[str, str], list[RecurrenceOccurrence]] = defaultdict(list)
    for occurrence in occurrences:
        if not isinstance(occurrence, RecurrenceOccurrence):
            raise TypeError("M16 occurrences must be RecurrenceOccurrence values")
        grouped[(occurrence.detector, occurrence.activity_fingerprint)].append(occurrence)

    reductions: list[M16ProjectConcentration] = []
    for (detector, fingerprint), population in sorted(grouped.items()):
        projects = Counter(item.project_id for item in population if item.project_id is not None)
        attributed_count = sum(projects.values())
        unresolved_count = len(population) - attributed_count
        top_project_id, top_project_count = (
            min(projects.items(), key=lambda item: (-item[1], item[0])) if projects else (None, 0)
        )
        reductions.append(
            M16ProjectConcentration(
                detector=detector,
                activity_fingerprint=fingerprint,
                total_count=len(population),
                attributed_count=attributed_count,
                unresolved_count=unresolved_count,
                top_project_id=top_project_id,
                top_project_count=top_project_count,
                top_project_percentage=(top_project_count * 100 / attributed_count)
                if attributed_count
                else None,
            )
        )
    return tuple(reductions)


def build_proof(
    *,
    boundary: RecurrenceProofBoundary,
    m16: Iterable[M16ProjectConcentration],
    m13: Iterable[M13Availability],
    provenance: EvidenceProvenance,
) -> RecurrenceExperimentProof:
    """Build an honest E0 proof; M13 absence always withholds task recurrence."""
    reductions = tuple(m16)
    availability = tuple(m13)
    total = sum(row.total_count for row in reductions)
    attributed = sum(row.attributed_count for row in reductions)
    unresolved = sum(row.unresolved_count for row in reductions)
    ambiguous_routes = sum(row.state is M13TaskIdentityState.AMBIGUOUS for row in availability)
    return RecurrenceExperimentProof(
        experiment_id=RecurrenceExperimentId.WAVE_A,
        run_id=boundary.run_id,
        result=ExperimentResult.BLOCKED,
        provenance=provenance,
        source_start=boundary.source_start,
        source_end=boundary.source_end,
        source_boundary=boundary.source_boundary,
        metrics={
            "m16_total_count": total,
            "m16_attributed_count": attributed,
            "m16_unresolved_count": unresolved,
            "m13_absent_route_count": len(availability) - ambiguous_routes,
            "m13_ambiguous_route_count": ambiguous_routes,
        },
        assertions={
            "m16_population_conserved": total == attributed + unresolved,
            "m16_source_time_ns_bound": boundary.m16_source_time_ns_bound,
            "m13_durable_task_identity_available": False,
        },
        evidence_ids=(),
        blocked_boundaries=(_MISSING_DURABLE_AUTHORITY,),
        proposal="withhold-task-recurrence-until-durable-canonical-task-identity",
    )


def _require_token(value: object, label: str) -> None:
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        raise ValueError(f"{label} must be a privacy-safe token")


def _require_fingerprint(value: object) -> None:
    if not isinstance(value, str) or not _FINGERPRINT.fullmatch(value):
        raise ValueError("fingerprint must be a privacy-safe SHA-256 fingerprint")

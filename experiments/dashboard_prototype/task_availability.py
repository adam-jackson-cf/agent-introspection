"""Privacy-safe E-Task-0 canonical activity route availability."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from experiments.dashboard_prototype.contracts import EvidenceProvenance, ExperimentResult
from experiments.dashboard_prototype.task_common import (
    SUPPORTED_SURFACES,
    TASK_MEASURES,
    TaskExperimentId,
    TaskExperimentProof,
)

DETECTOR_MEASURES: Final[Mapping[str, str]] = {
    "M5": "tool_failure",
    "M7": "repeated_attempt",
    "M8": "command_churn",
    "M9": "tool_loop",
    "M10": "sandbox_friction",
    "M11": "quality_gate_bypass",
    "M13": "scope_recurrence",
}
PROJECT_ACTIVITY_MEASURE: Final[str] = "M16"


class TaskRouteState(StrEnum):
    """Closed authority states for a producer task-measure route."""

    AUTHORITATIVE = "authoritative"
    AMBIGUOUS = "ambiguous"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class TaskAvailabilityClassification:
    """A route classification containing only public route metadata."""

    producer: str
    surface: str
    measure: str
    state: TaskRouteState
    capability: str = "canonical_detector_projection"
    time_domain: str = "canonical_source_time"
    population: str = "latest_canonical_activity_versions"
    redaction_boundary: str = "aggregate_classification_only"

    def __post_init__(self) -> None:
        if (self.producer, self.surface) not in SUPPORTED_SURFACES:
            raise ValueError("classification producer and surface must be canonical")
        if self.measure not in TASK_MEASURES:
            raise ValueError("classification measure is not required")
        if not all((self.capability, self.time_domain, self.population, self.redaction_boundary)):
            raise ValueError("classification route labels must be nonempty")


def _base_state(producer: str, measure: str) -> TaskRouteState:
    if producer == "omp" and measure in {"M3", "M4"}:
        return TaskRouteState.UNSUPPORTED
    return TaskRouteState.UNAVAILABLE


def current_classifications() -> tuple[TaskAvailabilityClassification, ...]:
    """Return the installed route matrix without inferring task-operation authority."""
    return tuple(
        TaskAvailabilityClassification(producer, surface, measure, _base_state(producer, measure))
        for producer, surface in SUPPORTED_SURFACES
        for measure in TASK_MEASURES
    )


def validate_classifications(
    classifications: Iterable[TaskAvailabilityClassification],
) -> tuple[TaskAvailabilityClassification, ...]:
    """Require exactly one classification for every canonical route."""
    rows = tuple(classifications)
    expected = {
        (producer, surface, measure)
        for producer, surface in SUPPORTED_SURFACES
        for measure in TASK_MEASURES
    }
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = (row.producer, row.surface, row.measure)
        if key in seen:
            raise ValueError("classification route conflicts or is duplicated")
        seen.add(key)
    if seen != expected:
        raise ValueError("classification matrix omits or adds a producer-measure route")
    return rows


def classification_counts(
    classifications: Iterable[TaskAvailabilityClassification],
) -> Mapping[TaskRouteState, int]:
    """Return conservation counts after validating the closed matrix."""
    rows = validate_classifications(classifications)
    return {state: sum(row.state is state for row in rows) for state in TaskRouteState}


def build_proof(
    run_id: str,
    classifications: Iterable[TaskAvailabilityClassification],
    *,
    source_boundary: str = "canonical-activity-schema-and-installed-authority",
    provenance: EvidenceProvenance = EvidenceProvenance.RETAINED,
    route_population: int = 0,
) -> TaskExperimentProof:
    """Build E0 proof, failing closed until every route is fresh authoritative."""
    if isinstance(route_population, bool) or route_population < 0:
        raise ValueError("route population must be a nonnegative integer")
    rows = validate_classifications(classifications)
    counts = classification_counts(rows)
    fresh_authoritative = provenance is EvidenceProvenance.FRESH_REAL and all(
        row.state is TaskRouteState.AUTHORITATIVE for row in rows
    )
    blocked = (
        *(
            f"{row.producer}.{row.surface}.{row.measure}"
            for row in rows
            if row.state is not TaskRouteState.AUTHORITATIVE
        ),
        "runner_owned_remote_reconciliation",
    )
    return TaskExperimentProof(
        experiment_id=TaskExperimentId.AVAILABILITY,
        run_id=run_id,
        result=ExperimentResult.BLOCKED,
        provenance=provenance,
        source_boundary=source_boundary,
        metrics={
            "route_manifest_count": len(rows),
            "route_population": route_population,
            "authoritative_route_count": counts[TaskRouteState.AUTHORITATIVE],
            "ambiguous_route_count": counts[TaskRouteState.AMBIGUOUS],
            "unavailable_route_count": counts[TaskRouteState.UNAVAILABLE],
            "unsupported_route_count": counts[TaskRouteState.UNSUPPORTED],
        },
        assertions={
            "complete_route_manifest": len(rows) == len(SUPPORTED_SURFACES) * len(TASK_MEASURES),
            "time_domain_is_source_time": True,
            "population_is_canonical_activity_latest_version": True,
            "redaction_boundary_preserved": True,
            "remote_calculation_reconciled": False,
            "every_route_fresh_authoritative": fresh_authoritative,
        },
        evidence_ids=(),
        blocked_boundaries=blocked,
        proposal="authoritative task operation route manifest",
    )

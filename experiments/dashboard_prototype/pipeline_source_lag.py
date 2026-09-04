"""Pure, bounded source-lag oracle for E-Pipeline-3."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from itertools import pairwise
from statistics import median

from experiments.dashboard_prototype.contracts import (
    EvidenceProvenance,
    ExperimentResult,
    PrototypeContractError,
)
from experiments.dashboard_prototype.pipeline_common import (
    PipelineExperimentId,
    PipelineExperimentProof,
)


@dataclass(frozen=True, slots=True, order=True)
class SourceLagCohort:
    """The normative producer, surface, and signal population boundary."""

    producer: str
    surface: str
    signal: str

    def __post_init__(self) -> None:
        if not all((self.producer, self.surface, self.signal)):
            raise PrototypeContractError("source-lag cohort fields must be nonempty")

    @property
    def boundary(self) -> str:
        return f"{self.producer}/{self.surface}/{self.signal}"


@dataclass(frozen=True, slots=True)
class SourceLagScan:
    """One successful, already-allowlisted scan completion."""

    scan_id: str
    cohort: SourceLagCohort
    population_start: datetime
    population_end: datetime
    started_at: datetime
    completed_at: datetime
    extraction_bound: datetime
    capability_available: bool | None

    def __post_init__(self) -> None:
        if not self.scan_id:
            raise PrototypeContractError("source-lag scan identity must be nonempty")
        _require_ordered_instants(self.population_start, self.population_end)
        _require_ordered_instants(self.started_at, self.completed_at)
        _require_aware_instant(self.extraction_bound)
        if self.extraction_bound > self.completed_at:
            raise PrototypeContractError(
                "source-lag extraction bound must not exceed scan completion"
            )


@dataclass(frozen=True, slots=True)
class AuthoritativeSourceObservation:
    """A source observation bound to one scan's authoritative extraction upper bound."""

    observation_id: str
    scan_id: str
    cohort: SourceLagCohort
    source_time: datetime
    current_identity: str
    extraction_bound: datetime | None = None

    def __post_init__(self) -> None:
        if not self.observation_id or not self.scan_id or not self.current_identity:
            raise PrototypeContractError("source observations require exact identities")
        _require_aware_instant(self.source_time)
        if self.extraction_bound is not None:
            _require_aware_instant(self.extraction_bound)


class SourceLagDisposition(StrEnum):
    ACCEPTED = "accepted"
    MISSING_CAPABILITY = "missing-capability"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"


@dataclass(frozen=True, slots=True)
class SourceLagSelection:
    scan_id: str
    cohort: SourceLagCohort
    disposition: SourceLagDisposition
    selected_observation_id: str | None
    selected_current_identity: str | None
    lag_seconds: float | None
    negative_skew: bool


@dataclass(frozen=True, slots=True)
class SourceLagCohortMetrics:
    cohort: SourceLagCohort
    accepted: int
    missing_capability: int
    rejected: int
    duplicate: int
    p50_lag_seconds: float | None
    p95_lag_seconds: float | None
    n: int
    negative_skew_count: int

    @property
    def population(self) -> int:
        return self.accepted + self.missing_capability + self.rejected + self.duplicate


@dataclass(frozen=True, slots=True)
class SourceLagReduction:
    selections: tuple[SourceLagSelection, ...]
    cohorts: tuple[SourceLagCohortMetrics, ...]
    blocked_boundaries: tuple[str, ...]


def reduce_source_lag(
    scans: Iterable[SourceLagScan],
    observations: Iterable[AuthoritativeSourceObservation],
    normative_cohorts: Iterable[SourceLagCohort],
) -> SourceLagReduction:
    """Reduce bounded scans deterministically, selecting each scan's own latest source."""
    cohort_set = frozenset(normative_cohorts)
    _require_normative_cohorts(cohort_set)
    scan_rows = tuple(scans)
    observation_rows = tuple(observations)
    _require_normative_population(cohort_set, scan_rows, observation_rows)
    _require_observation_scan_bindings(scan_rows, observation_rows)
    observations_by_scan_cohort = _observations_by_scan_cohort(cohort_set, observation_rows)
    selections, blocked = _select_source_lag_scans(
        cohort_set, scan_rows, observations_by_scan_cohort
    )
    metrics = tuple(_cohort_metrics(cohort, selections) for cohort in sorted(cohort_set))
    return SourceLagReduction(
        selections=selections, cohorts=metrics, blocked_boundaries=tuple(sorted(blocked))
    )


def build_source_lag_proof(
    run_id: str,
    provenance: EvidenceProvenance,
    source_boundary: str,
    reduction: SourceLagReduction,
) -> PipelineExperimentProof:
    """Build a fail-closed proof from an already reduced bounded population."""
    accepted = tuple(
        selection
        for selection in reduction.selections
        if selection.disposition is SourceLagDisposition.ACCEPTED
    )
    population = sum(metric.population for metric in reduction.cohorts)
    conserved = population == len(reduction.selections)
    applicable = any(
        metric.accepted or metric.rejected or metric.duplicate for metric in reduction.cohorts
    )
    all_capability_absent = population > 0 and all(
        metric.missing_capability == metric.population for metric in reduction.cohorts
    )
    assertions = {
        "population_conserved": conserved,
        "current_identity_selected": all(
            selection.selected_current_identity is not None for selection in accepted
        ),
        "applicable_population_reduced": bool(accepted) if applicable else all_capability_absent,
    }
    if reduction.blocked_boundaries:
        result = ExperimentResult.BLOCKED
    elif all_capability_absent:
        result = ExperimentResult.NOT_APPLICABLE
    elif all(assertions.values()) and provenance is EvidenceProvenance.FRESH_REAL:
        result = ExperimentResult.PROVEN
    else:
        result = ExperimentResult.FAILED
    latency_lags = [
        selection.lag_seconds
        for selection in accepted
        if selection.lag_seconds is not None and not selection.negative_skew
    ]
    metrics = {
        "population": population,
        "accepted": len(accepted),
        "missing_capability": sum(metric.missing_capability for metric in reduction.cohorts),
        "rejected": sum(metric.rejected for metric in reduction.cohorts),
        "duplicate": sum(metric.duplicate for metric in reduction.cohorts),
        "n": len(latency_lags),
        "p50_lag_seconds": float(median(latency_lags)) if latency_lags else 0.0,
        "p95_lag_seconds": _nearest_rank(latency_lags, 95) if latency_lags else 0.0,
        "negative_skew_count": sum(selection.negative_skew for selection in accepted),
    }
    return PipelineExperimentProof(
        experiment_id=PipelineExperimentId.SOURCE_LAG,
        run_id=run_id,
        result=result,
        provenance=provenance,
        source_boundary=source_boundary,
        metrics=metrics,
        assertions=assertions,
        evidence_ids=tuple(selection.scan_id for selection in accepted),
        blocked_boundaries=reduction.blocked_boundaries,
        proposal="Bound each source-lag reading to the scan extraction boundary.",
    )


def _require_normative_cohorts(cohorts: frozenset[SourceLagCohort]) -> None:
    if not cohorts:
        raise PrototypeContractError("source-lag requires at least one normative cohort")


def _require_normative_population(
    cohorts: frozenset[SourceLagCohort],
    scans: tuple[SourceLagScan, ...],
    observations: tuple[AuthoritativeSourceObservation, ...],
) -> None:
    if any(scan.cohort not in cohorts for scan in scans):
        raise PrototypeContractError("scan cohort is outside the normative population")
    if any(observation.cohort not in cohorts for observation in observations):
        raise PrototypeContractError("source cohort is outside the normative population")


def _require_observation_scan_bindings(
    scans: tuple[SourceLagScan, ...],
    observations: tuple[AuthoritativeSourceObservation, ...],
) -> None:
    scan_keys = {(scan.scan_id, scan.cohort) for scan in scans}
    if any(
        (observation.scan_id, observation.cohort) not in scan_keys for observation in observations
    ):
        raise PrototypeContractError("source observation is not bound to a source-lag scan")


def _observations_by_scan_cohort(
    cohorts: frozenset[SourceLagCohort],
    observations: tuple[AuthoritativeSourceObservation, ...],
) -> dict[tuple[str, SourceLagCohort], tuple[AuthoritativeSourceObservation, ...]]:
    grouped: dict[tuple[str, SourceLagCohort], list[AuthoritativeSourceObservation]] = {}
    for observation in observations:
        if observation.cohort in cohorts:
            grouped.setdefault((observation.scan_id, observation.cohort), []).append(observation)
    return {
        key: tuple(
            sorted(
                rows,
                key=lambda observation: (
                    observation.source_time,
                    observation.observation_id,
                ),
            )
        )
        for key, rows in grouped.items()
    }


def _select_source_lag_scans(
    cohorts: frozenset[SourceLagCohort],
    scans: tuple[SourceLagScan, ...],
    observations_by_scan_cohort: dict[
        tuple[str, SourceLagCohort], tuple[AuthoritativeSourceObservation, ...]
    ],
) -> tuple[tuple[SourceLagSelection, ...], set[str]]:
    population_scans = tuple(scan for scan in scans if _is_population_member(scan))
    _require_nonconflicting_scan_duplicates(population_scans)
    scans_by_id: dict[str, list[SourceLagScan]] = {}
    for scan in population_scans:
        scans_by_id.setdefault(scan.scan_id, []).append(scan)

    selections: list[SourceLagSelection] = []
    blocked: dict[tuple[str, SourceLagCohort], int] = {}
    if not scans_by_id:
        for cohort in cohorts:
            _record_blocker(blocked, "scan-completion", cohort)
    for scan_id, scan_rows in sorted(
        scans_by_id.items(),
        key=lambda item: (
            min(scan.completed_at for scan in item[1]),
            item[0],
        ),
    ):
        seen_scan_keys: set[tuple[str, SourceLagCohort]] = set()
        present_cohorts = {scan.cohort for scan in scan_rows}
        for scan in sorted(scan_rows, key=lambda row: row.cohort.boundary):
            selection, reason = _select_scan_source(
                scan,
                seen_scan_keys,
                observations_by_scan_cohort.get((scan.scan_id, scan.cohort), ()),
            )
            selections.append(selection)
            if reason is not None:
                _record_blocker(blocked, reason, scan.cohort)
        for cohort in sorted(cohorts - present_cohorts):
            selections.append(_selection_for_cohort(scan_id, cohort, SourceLagDisposition.REJECTED))
            _record_blocker(blocked, "scan-completion", cohort)
    return tuple(selections), _summarize_blockers(blocked)


def _record_blocker(
    blocked: dict[tuple[str, SourceLagCohort], int],
    reason: str,
    cohort: SourceLagCohort,
) -> None:
    key = (reason, cohort)
    blocked[key] = blocked.get(key, 0) + 1


def _summarize_blockers(blocked: dict[tuple[str, SourceLagCohort], int]) -> set[str]:
    return {
        f"{reason}:{cohort.boundary}:count={count}" for (reason, cohort), count in blocked.items()
    }


def _require_nonconflicting_scan_duplicates(scans: Iterable[SourceLagScan]) -> None:
    unique_scans: dict[tuple[str, SourceLagCohort], SourceLagScan] = {}
    for scan in scans:
        key = (scan.scan_id, scan.cohort)
        existing = unique_scans.setdefault(key, scan)
        if existing != scan:
            raise PrototypeContractError("conflicting source-lag scans share an identity")


def _select_scan_source(
    scan: SourceLagScan,
    seen_scan_keys: set[tuple[str, SourceLagCohort]],
    observations: tuple[AuthoritativeSourceObservation, ...],
) -> tuple[SourceLagSelection, str | None]:
    scan_key = (scan.scan_id, scan.cohort)
    if scan_key in seen_scan_keys:
        return _selection(scan, SourceLagDisposition.DUPLICATE), None
    seen_scan_keys.add(scan_key)
    if scan.capability_available is False:
        return _selection(scan, SourceLagDisposition.MISSING_CAPABILITY), None
    if scan.capability_available is None:
        return _selection(scan, SourceLagDisposition.REJECTED), "capability"
    if any(observation.extraction_bound != scan.extraction_bound for observation in observations):
        return _selection(scan, SourceLagDisposition.REJECTED), "source-extraction-bound"
    selected = _latest_scan_observation(observations)
    if selected is None:
        return _selection(scan, SourceLagDisposition.REJECTED), "source-observation"
    return _accepted_selection(scan, selected), None


def _latest_scan_observation(
    observations: tuple[AuthoritativeSourceObservation, ...],
) -> AuthoritativeSourceObservation | None:
    return observations[-1] if observations else None


def _accepted_selection(
    scan: SourceLagScan, observation: AuthoritativeSourceObservation
) -> SourceLagSelection:
    lag_seconds = (scan.extraction_bound - observation.source_time).total_seconds()
    return SourceLagSelection(
        scan_id=scan.scan_id,
        cohort=scan.cohort,
        disposition=SourceLagDisposition.ACCEPTED,
        selected_observation_id=observation.observation_id,
        selected_current_identity=observation.current_identity,
        lag_seconds=lag_seconds,
        negative_skew=lag_seconds < 0,
    )


def _is_population_member(scan: SourceLagScan) -> bool:
    return scan.population_start < scan.completed_at <= scan.population_end


def _selection(scan: SourceLagScan, disposition: SourceLagDisposition) -> SourceLagSelection:
    return _selection_for_cohort(scan.scan_id, scan.cohort, disposition)


def _selection_for_cohort(
    scan_id: str, cohort: SourceLagCohort, disposition: SourceLagDisposition
) -> SourceLagSelection:
    return SourceLagSelection(scan_id, cohort, disposition, None, None, None, False)


def _cohort_metrics(
    cohort: SourceLagCohort, selections: Iterable[SourceLagSelection]
) -> SourceLagCohortMetrics:
    rows = tuple(row for row in selections if row.cohort == cohort)
    accepted = tuple(row for row in rows if row.disposition is SourceLagDisposition.ACCEPTED)
    lags = [
        row.lag_seconds for row in accepted if row.lag_seconds is not None and not row.negative_skew
    ]
    return SourceLagCohortMetrics(
        cohort=cohort,
        accepted=len(accepted),
        missing_capability=sum(
            row.disposition is SourceLagDisposition.MISSING_CAPABILITY for row in rows
        ),
        rejected=sum(row.disposition is SourceLagDisposition.REJECTED for row in rows),
        duplicate=sum(row.disposition is SourceLagDisposition.DUPLICATE for row in rows),
        p50_lag_seconds=float(median(lags)) if lags else None,
        p95_lag_seconds=_nearest_rank(lags, 95) if lags else None,
        n=len(lags),
        negative_skew_count=sum(row.negative_skew for row in accepted),
    )


def _nearest_rank(values: list[float], percentile: int) -> float:
    ordered = sorted(values)
    return ordered[(len(ordered) * percentile + 99) // 100 - 1]


def _require_ordered_instants(*instants: datetime) -> None:
    for instant in instants:
        _require_aware_instant(instant)
    if any(left > right for left, right in pairwise(instants)):
        raise PrototypeContractError("scan instants must be ordered")


def _require_aware_instant(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PrototypeContractError("source-lag instants must be timezone-aware")

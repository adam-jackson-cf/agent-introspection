"""Pure E-Attribution-4 lifecycle-delay reducer."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from statistics import median

from experiments.dashboard_prototype.attribution_common import (
    AttributionExperimentId,
    AttributionExperimentProof,
)
from experiments.dashboard_prototype.contracts import (
    SUPPORTED_PRODUCERS,
    EvidenceProvenance,
    ExperimentResult,
    PrototypeContractError,
)


@dataclass(frozen=True, slots=True, order=True)
class LifecycleDelayCohort:
    producer: str
    surface: str

    def __post_init__(self) -> None:
        if self.producer not in SUPPORTED_PRODUCERS or not self.surface:
            raise PrototypeContractError("lifecycle-delay cohort is unsupported")

    @property
    def boundary(self) -> str:
        return f"{self.producer}/{self.surface}"


@dataclass(frozen=True, slots=True)
class AuthoritativeSourceEvent:
    event_id: str
    cohort: LifecycleDelayCohort
    native_session_id: str
    source_time: datetime

    def __post_init__(self) -> None:
        _require_identity(self.event_id, self.native_session_id)
        _require_instant(self.source_time)


@dataclass(frozen=True, slots=True)
class AcceptedLifecycleInterval:
    event_id: str
    cohort: LifecycleDelayCohort
    native_session_id: str
    interval_start: datetime
    interval_end: datetime | None

    def __post_init__(self) -> None:
        _require_identity(self.event_id, self.native_session_id)
        _require_instant(self.interval_start)
        if self.interval_end is not None:
            _require_instant(self.interval_end)
            if self.interval_end < self.interval_start:
                raise PrototypeContractError("lifecycle interval end precedes its start")


@dataclass(frozen=True, slots=True)
class LifecycleDelaySelection:
    cohort: LifecycleDelayCohort
    source_event_id: str
    native_session_id: str
    source_time: datetime
    interval_event_id: str | None
    interval_start: datetime | None
    delay_seconds: float | None
    matched: bool
    negative_skew: bool


@dataclass(frozen=True, slots=True)
class LifecycleDelayMetrics:
    cohort: LifecycleDelayCohort
    selected_sessions: int
    matched_sessions: int
    negative_skew_sessions: int
    n: int
    p50_delay_seconds: float | None
    p95_delay_seconds: float | None


@dataclass(frozen=True, slots=True)
class LifecycleDelayReduction:
    selections: tuple[LifecycleDelaySelection, ...]
    cohorts: tuple[LifecycleDelayMetrics, ...]
    blocked_boundaries: tuple[str, ...]
    contradictory: bool = False


def reduce_lifecycle_delay(
    source_events: Iterable[AuthoritativeSourceEvent],
    intervals: Iterable[AcceptedLifecycleInterval],
    cohorts: Iterable[LifecycleDelayCohort],
    start: datetime,
    end: datetime,
) -> LifecycleDelayReduction:
    """Select each exact session's first bounded source and containing interval."""
    _require_window(start, end)
    cohort_set = frozenset(cohorts)
    if not cohort_set:
        raise PrototypeContractError("lifecycle-delay requires normative cohorts")
    sources = tuple(source_events)
    interval_rows = tuple(intervals)
    if any(row.cohort not in cohort_set for row in sources + interval_rows):
        raise PrototypeContractError("lifecycle-delay input is outside normative cohorts")
    if _conflicts(sources) or _conflicts(interval_rows):
        return LifecycleDelayReduction((), (), (), True)
    first_sources = _first_sources(sources, start, end)
    by_session: dict[tuple[LifecycleDelayCohort, str], list[AcceptedLifecycleInterval]] = {}
    for interval in interval_rows:
        by_session.setdefault((interval.cohort, interval.native_session_id), []).append(interval)
    selections: list[LifecycleDelaySelection] = []
    blocked: set[str] = set()
    for source in first_sources:
        matches = [
            interval
            for interval in by_session.get((source.cohort, source.native_session_id), ())
            if interval.interval_start <= source.source_time
            and (interval.interval_end is None or source.source_time < interval.interval_end)
        ]
        if len(matches) > 1:
            blocked.add(f"lifecycle-authority:{source.cohort.boundary}")
            selections.append(
                LifecycleDelaySelection(
                    source.cohort,
                    source.event_id,
                    source.native_session_id,
                    source.source_time,
                    None,
                    None,
                    None,
                    False,
                    False,
                )
            )
            continue
        if not matches:
            blocked.add(f"lifecycle-authority:{source.cohort.boundary}")
            selections.append(
                LifecycleDelaySelection(
                    source.cohort,
                    source.event_id,
                    source.native_session_id,
                    source.source_time,
                    None,
                    None,
                    None,
                    False,
                    False,
                )
            )
            continue
        interval = matches[0]
        delay = (source.source_time - interval.interval_start).total_seconds()
        selections.append(
            LifecycleDelaySelection(
                source.cohort,
                source.event_id,
                source.native_session_id,
                source.source_time,
                interval.event_id,
                interval.interval_start,
                delay,
                True,
                delay < 0,
            )
        )
    metrics = tuple(_metrics(cohort, selections) for cohort in sorted(cohort_set))
    return LifecycleDelayReduction(tuple(selections), metrics, tuple(sorted(blocked)))


def build_lifecycle_delay_proof(
    run_id: str,
    provenance: EvidenceProvenance,
    source_boundary: str,
    reduction: LifecycleDelayReduction,
) -> AttributionExperimentProof:
    """Build the fail-closed E-Attribution-4 proof envelope."""
    selected = len(reduction.selections)
    matched = sum(metric.matched_sessions for metric in reduction.cohorts)
    assertions = {
        "population_conserved": selected
        == sum(metric.selected_sessions for metric in reduction.cohorts),
        "first_source_selected": len(
            {(row.cohort, row.native_session_id) for row in reduction.selections}
        )
        == selected,
        "containing_lifecycle_matched": matched == selected,
        "no_contradictory_authority": not reduction.contradictory,
    }
    if reduction.contradictory:
        result = ExperimentResult.FAILED
    elif reduction.blocked_boundaries:
        result = ExperimentResult.BLOCKED
    elif all(assertions.values()) and provenance is EvidenceProvenance.FRESH_REAL:
        result = ExperimentResult.PROVEN
    else:
        result = ExperimentResult.FAILED
    metrics = {
        "selected_sessions": selected,
        "matched_sessions": matched,
        "n": sum(metric.n for metric in reduction.cohorts),
        "negative_skew_sessions": sum(
            metric.negative_skew_sessions for metric in reduction.cohorts
        ),
    }
    return AttributionExperimentProof(
        experiment_id=AttributionExperimentId.LIFECYCLE_DELAY,
        run_id=run_id,
        result=result,
        provenance=provenance,
        source_boundary=source_boundary,
        metrics=metrics,
        assertions=assertions,
        evidence_ids=tuple(row.source_event_id for row in reduction.selections if row.matched),
        blocked_boundaries=reduction.blocked_boundaries,
        proposal=(
            "Measure first authoritative source delay from its accepted "
            "containing lifecycle interval."
        ),
    )


def _first_sources(
    sources: tuple[AuthoritativeSourceEvent, ...], start: datetime, end: datetime
) -> tuple[AuthoritativeSourceEvent, ...]:
    candidates = (row for row in sources if start < row.source_time <= end)
    selected: dict[tuple[LifecycleDelayCohort, str], AuthoritativeSourceEvent] = {}
    for source in sorted(candidates, key=lambda row: (row.source_time, row.event_id)):
        selected.setdefault((source.cohort, source.native_session_id), source)
    return tuple(
        sorted(selected.values(), key=lambda row: (row.cohort, row.source_time, row.event_id))
    )


def _metrics(
    cohort: LifecycleDelayCohort, selections: list[LifecycleDelaySelection]
) -> LifecycleDelayMetrics:
    rows = [row for row in selections if row.cohort == cohort]
    matched = [row for row in rows if row.matched]
    delays = sorted(
        row.delay_seconds
        for row in matched
        if row.delay_seconds is not None and not row.negative_skew
    )
    return LifecycleDelayMetrics(
        cohort,
        len(rows),
        len(matched),
        sum(row.negative_skew for row in matched),
        len(delays),
        float(median(delays)) if delays else None,
        _nearest_rank(delays, 95) if delays else None,
    )


def _nearest_rank(values: list[float], percentile: int) -> float:
    return values[max(0, (len(values) * percentile + 99) // 100 - 1)]


def _conflicts(rows: tuple[AuthoritativeSourceEvent | AcceptedLifecycleInterval, ...]) -> bool:
    seen: dict[str, AuthoritativeSourceEvent | AcceptedLifecycleInterval] = {}
    for row in rows:
        existing = seen.setdefault(row.event_id, row)
        if existing != row:
            return True
    return False


def _require_identity(*values: str) -> None:
    if any(not value for value in values):
        raise PrototypeContractError("lifecycle-delay identities must be nonempty")


def _require_instant(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PrototypeContractError("lifecycle-delay instants must be timezone-aware")


def _require_window(start: datetime, end: datetime) -> None:
    _require_instant(start)
    _require_instant(end)
    if start >= end:
        raise PrototypeContractError("lifecycle-delay window must be ordered")

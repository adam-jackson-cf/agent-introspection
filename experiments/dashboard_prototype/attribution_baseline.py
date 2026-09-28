"""Pure E-Attribution-1 reducer for the retained OMP and Codex CLI authority."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from itertools import chain
from typing import Final

from experiments.dashboard_prototype.attribution_common import (
    AttributionExperimentId,
    AttributionExperimentProof,
)
from experiments.dashboard_prototype.contracts import (
    EvidenceProvenance,
    ExperimentResult,
)

_SUPPORTED: Final = frozenset(("omp", "codex-cli"))
_REQUIRED_SCENARIOS: Final = (
    "fresh",
    "resume",
    "end",
    "concurrent_projects",
    "non_git",
    "workspace_change",
)


class CapabilityState(StrEnum):
    PASSED = "passed"
    NOT_EXPOSED = "not_exposed"


@dataclass(frozen=True, slots=True)
class ProducerAuthority:
    """Privacy-safe retained proof for a supported producer's native identity boundary."""

    producer: str
    surface: str
    evidence_id: str
    native_matches_correlation: bool
    project_id: str
    project_name: str
    project_kind: str
    capabilities: Mapping[str, CapabilityState]

    def validate(self) -> None:
        if self.producer not in _SUPPORTED or not self.surface or not self.evidence_id:
            raise ValueError("unsupported or incomplete retained producer authority")
        if not self.native_matches_correlation:
            raise ValueError("native and correlation identities must match exactly")
        if len(self.project_id) != 64 or not self.project_name or self.project_kind != "git":
            raise ValueError("retained project tuple is incomplete")
        if set(self.capabilities) != set(_REQUIRED_SCENARIOS):
            raise ValueError("retained authority must state every scenario capability")


@dataclass(frozen=True, slots=True)
class CanonicalActivityVersion:
    """One immutable native source-time version with exact private identities."""

    activity_id: str
    version: int
    source_time_ns: int
    producer: str
    surface: str
    native_session_id: str
    state: str
    project_id: str | None
    reason_code: str | None
    attribution_method: str = "none"

    def validate(self) -> None:
        if (
            not self.activity_id
            or self.version < 1
            or type(self.source_time_ns) is not int
            or self.source_time_ns < 0
        ):
            raise ValueError("activity version lacks immutable identity, version, or source time")
        if self.producer not in _SUPPORTED or not self.surface or not self.native_session_id:
            raise ValueError("activity has an unsupported or incomplete native identity")
        if self.state not in {"attributed", "unresolved"}:
            raise ValueError("activity state must be attributed or unresolved")
        if (self.state == "attributed") != (self.project_id is not None):
            raise ValueError("activity attribution state and project are contradictory")
        if self.state == "unresolved" and not self.reason_code:
            raise ValueError("unresolved activity requires an allowlisted diagnostic")


@dataclass(frozen=True, slots=True)
class AttributionPopulation:
    eligible: int
    attributed: int
    unresolved: int
    distinct_projects: int
    diagnostics: Mapping[str, int]

    @property
    def conserves(self) -> bool:
        return self.eligible == self.attributed + self.unresolved


@dataclass(frozen=True, slots=True)
class AttributionBaselineReduction:
    authorities: tuple[ProducerAuthority, ...]
    latest_activities: tuple[CanonicalActivityVersion, ...]
    p7: AttributionPopulation
    p8: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class SourceSessionObservation:
    producer: str
    surface: str
    native_session_id: str
    source_time_ns: int
    source_id: str | None = None


@dataclass(frozen=True, slots=True)
class LifecycleSessionObservation:
    producer: str
    surface: str
    native_session_id: str
    interval_start: datetime | None
    interval_end: datetime | None
    accepted_events: tuple[tuple[datetime, str], ...] = ()
    lifecycle_event_id: str | None = None


@dataclass(frozen=True, slots=True)
class P5Cohort:
    source_sessions: int
    source_with_lifecycle: int
    lifecycle_sessions: int | None
    lifecycle_with_source: int | None

    @property
    def source_conserves(self) -> bool:
        return 0 <= self.source_with_lifecycle <= self.source_sessions

    @property
    def lifecycle_conserves(self) -> bool:
        return self.lifecycle_sessions is None or (
            self.lifecycle_with_source is not None
            and 0 <= self.lifecycle_with_source <= self.lifecycle_sessions
        )


@dataclass(frozen=True, slots=True)
class P5SessionMembership:
    producer: str
    surface: str
    native_session_id: str
    direction: str
    source_time_ns: int
    matched: bool
    source: SourceSessionObservation | None
    intervals: tuple[LifecycleSessionObservation, ...]


def _containing_interval(
    source: SourceSessionObservation, intervals: Sequence[LifecycleSessionObservation]
) -> LifecycleSessionObservation | None:
    matched = tuple(
        interval
        for interval in intervals
        if interval.interval_start is not None
        and _epoch_ns(interval.interval_start) <= source.source_time_ns
        and (
            interval.interval_end is None
            or source.source_time_ns < _epoch_ns(interval.interval_end)
        )
    )
    if len(matched) > 1:
        raise ValueError("ambiguous accepted lifecycle intervals")
    return matched[0] if matched else None


def p5_session_members(
    sources: Sequence[SourceSessionObservation],
    lifecycles: Sequence[LifecycleSessionObservation],
    *,
    start: datetime,
    end: datetime,
) -> tuple[P5SessionMembership, ...]:
    """Select first-source and any-containing-source directional session cohorts."""
    start_ns, end_ns = _epoch_ns(start), _epoch_ns(end)
    source_groups: dict[tuple[str, str, str], list[SourceSessionObservation]] = {}
    lifecycle_groups: dict[tuple[str, str, str], list[LifecycleSessionObservation]] = {}
    for source in sources:
        if start_ns < source.source_time_ns <= end_ns:
            source_groups.setdefault(
                (source.producer, source.surface, source.native_session_id), []
            ).append(source)
    for interval in lifecycles:
        lifecycle_groups.setdefault(
            (interval.producer, interval.surface, interval.native_session_id), []
        ).append(interval)
    members: list[P5SessionMembership] = []
    for key in sorted(source_groups.keys() | lifecycle_groups.keys()):
        raw_sources = source_groups.get(key, ())
        intervals = tuple(lifecycle_groups.get(key, ()))
        if raw_sources:
            first = min(raw_sources, key=lambda row: (row.source_time_ns, row.source_id or ""))
            members.append(
                P5SessionMembership(
                    *key,
                    "source_to_lifecycle",
                    first.source_time_ns,
                    _containing_interval(first, intervals) is not None,
                    first,
                    intervals,
                )
            )
        event_times = {
            instant
            for interval in intervals
            for instant, _ in interval.accepted_events
            if start < instant <= end
        }
        if event_times:
            witnesses = (
                source
                for source in raw_sources
                if _containing_interval(source, intervals) is not None
            )
            witness = min(
                witnesses, key=lambda row: (row.source_time_ns, row.source_id or ""), default=None
            )
            members.append(
                P5SessionMembership(
                    *key,
                    "lifecycle_to_source",
                    _epoch_ns(min(event_times)),
                    witness is not None,
                    witness,
                    intervals,
                )
            )
    return tuple(members)


def reduce_p5_directional_cohorts(
    sources: Sequence[SourceSessionObservation],
    lifecycles: Sequence[LifecycleSessionObservation],
    *,
    start: datetime,
    end: datetime,
) -> Mapping[str, P5Cohort]:
    """Reconcile exact producer/surface/native-session membership in a bounded range."""
    observations: chain[SourceSessionObservation | LifecycleSessionObservation] = chain(
        sources, lifecycles
    )
    counts = {(row.producer, row.surface): [0, 0, 0, 0] for row in observations}
    for member in p5_session_members(sources, lifecycles, start=start, end=end):
        index = 0 if member.direction == "source_to_lifecycle" else 2
        cohort = counts[(member.producer, member.surface)]
        cohort[index] += 1
        cohort[index + 1] += int(member.matched)
    return {
        f"{producer}.{surface}": P5Cohort(*cohort)
        for (producer, surface), cohort in sorted(counts.items())
    }


@dataclass(frozen=True, slots=True)
class AttributionBaselineProofInput:
    authorities: Sequence[ProducerAuthority] | None
    activities: Sequence[CanonicalActivityVersion] | None
    sources: Sequence[SourceSessionObservation] | None
    lifecycles: Sequence[LifecycleSessionObservation] | None
    start: datetime | None
    end: datetime | None


def _validated_authorities(
    authorities: Sequence[ProducerAuthority],
) -> tuple[ProducerAuthority, ...]:
    by_producer: dict[str, ProducerAuthority] = {}
    for authority in authorities:
        authority.validate()
        if authority.producer in by_producer:
            raise ValueError("retained authority producer identity is not immutable")
        by_producer[authority.producer] = authority
    if set(by_producer) != _SUPPORTED:
        raise ValueError("retained authority must cover exactly OMP and Codex CLI")
    return tuple(sorted(by_producer.values(), key=lambda value: value.producer))


def _latest_activities(
    activities: Sequence[CanonicalActivityVersion], start: datetime, end: datetime
) -> tuple[CanonicalActivityVersion, ...]:
    latest: dict[str, CanonicalActivityVersion] = {}
    for activity in activities:
        activity.validate()
        previous = latest.get(activity.activity_id)
        if previous is not None and previous.source_time_ns != activity.source_time_ns:
            raise ValueError("activity versions must retain one immutable source time")
        if previous is None or activity.version > previous.version:
            latest[activity.activity_id] = activity
    start_ns, end_ns = _epoch_ns(start), _epoch_ns(end)
    selected = (row for row in latest.values() if start_ns < row.source_time_ns <= end_ns)
    return tuple(sorted(selected, key=lambda row: (row.source_time_ns, row.activity_id)))


def _epoch_ns(value: datetime) -> int:
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


def _population(
    selected: Sequence[CanonicalActivityVersion],
) -> AttributionPopulation:
    attributed = sum(row.state == "attributed" for row in selected)
    diagnostics: dict[str, int] = {}
    for row in selected:
        if row.state == "unresolved":
            assert row.reason_code is not None
            diagnostics[row.reason_code] = diagnostics.get(row.reason_code, 0) + 1
    return AttributionPopulation(
        len(selected),
        attributed,
        len(selected) - attributed,
        len({row.project_id for row in selected if row.project_id}),
        diagnostics,
    )


def reduce_attribution_baseline(
    authorities: Sequence[ProducerAuthority],
    activities: Sequence[CanonicalActivityVersion],
    *,
    start: datetime,
    end: datetime,
) -> AttributionBaselineReduction:
    """Select latest valid activity versions in ``start < source_time <= end``."""
    if start >= end:
        raise ValueError("attribution range requires start before end")
    retained = _validated_authorities(authorities)
    selected = _latest_activities(activities, start, end)
    p7 = _population(selected)
    return AttributionBaselineReduction(
        retained,
        selected,
        p7,
        dict(sorted(p7.diagnostics.items())),
    )


def _complete_p5_cohorts(
    cohorts: Mapping[str, P5Cohort],
    authorities: Sequence[ProducerAuthority],
) -> dict[str, P5Cohort]:
    complete = dict(cohorts)
    for authority in authorities:
        complete.setdefault(
            f"{authority.producer}.{authority.surface}",
            P5Cohort(0, 0, 0, 0),
        )
    return complete


def build_attribution_baseline_proof(
    *,
    run_id: str,
    provenance: EvidenceProvenance,
    source_boundary: str,
    proof_input: AttributionBaselineProofInput,
) -> AttributionExperimentProof:
    """Build E-Attribution-1 proof; missing activity authority blocks P7/P8 only."""
    blocked: list[str] = []
    if not proof_input.authorities:
        blocked.append("retained_producer_authority")
    if proof_input.start is None or proof_input.end is None:
        blocked.append("source_time_range")
    assertions = {"fresh_real_provenance": provenance is EvidenceProvenance.FRESH_REAL}
    if blocked:
        return AttributionExperimentProof(
            AttributionExperimentId.BASELINE,
            run_id,
            ExperimentResult.BLOCKED,
            provenance,
            source_boundary,
            {},
            assertions,
            (),
            tuple(blocked),
            "Retain exact supported-producer authority before attribution cutover.",
        )
    assert proof_input.authorities is not None
    assert proof_input.start is not None
    assert proof_input.end is not None
    try:
        reduction = reduce_attribution_baseline(
            proof_input.authorities,
            proof_input.activities or (),
            start=proof_input.start,
            end=proof_input.end,
        )
    except ValueError as error:
        return AttributionExperimentProof(
            AttributionExperimentId.BASELINE,
            run_id,
            ExperimentResult.FAILED,
            provenance,
            source_boundary,
            {},
            {**assertions, "retained_authority_valid": False},
            (),
            (),
            f"Do not cut over: {error}.",
        )
    p5 = (
        reduce_p5_directional_cohorts(
            proof_input.sources or (),
            proof_input.lifecycles or (),
            start=proof_input.start,
            end=proof_input.end,
        )
        if proof_input.sources is not None and proof_input.lifecycles is not None
        else {}
    )
    p5 = _complete_p5_cohorts(p5, reduction.authorities)
    expected_p5_pairs = {
        f"{authority.producer}.{authority.surface}" for authority in reduction.authorities
    }
    assertions.update(
        {
            "retained_authority_valid": True,
            "p5_directional_capabilities_complete": set(p5) == expected_p5_pairs,
            "p5_population_conserves": all(
                cohort.source_conserves and cohort.lifecycle_conserves for cohort in p5.values()
            ),
            "p7_population_conserves": reduction.p7.conserves,
            "p8_uses_p7_population": sum(reduction.p8.values()) == reduction.p7.unresolved,
        }
    )
    metrics: dict[str, str | int | bool | None] = {
        "p7_eligible": reduction.p7.eligible,
        "p7_attributed": reduction.p7.attributed,
        "p7_unresolved": reduction.p7.unresolved,
        "p7_distinct_projects": reduction.p7.distinct_projects,
        "p8_diagnostic_groups": len(reduction.p8),
    }
    for authority in reduction.authorities:
        for scenario, state in sorted(authority.capabilities.items()):
            metrics[f"p5.{authority.producer}.{authority.surface}.{scenario}"] = state.value
    for key, cohort in p5.items():
        metrics[f"p5.{key}.source_sessions"] = cohort.source_sessions
        metrics[f"p5.{key}.source_with_lifecycle"] = cohort.source_with_lifecycle
        metrics[f"p5.{key}.lifecycle_sessions"] = cohort.lifecycle_sessions
        metrics[f"p5.{key}.lifecycle_with_source"] = cohort.lifecycle_with_source
    evidence_ids = tuple(authority.evidence_id for authority in reduction.authorities)
    if proof_input.sources is None or proof_input.lifecycles is None:
        return AttributionExperimentProof(
            AttributionExperimentId.BASELINE,
            run_id,
            ExperimentResult.BLOCKED,
            provenance,
            source_boundary,
            metrics,
            assertions,
            evidence_ids,
            ("current_source_membership",),
            "Retained identity is valid; block P5 until durable source membership exists.",
        )
    if proof_input.activities is None:
        return AttributionExperimentProof(
            AttributionExperimentId.BASELINE,
            run_id,
            ExperimentResult.BLOCKED,
            provenance,
            source_boundary,
            metrics,
            assertions,
            evidence_ids,
            ("current_activity_authority",),
            "Retained P5 is valid; block P7/P8 until current activity authority is available.",
        )
    result = ExperimentResult.PROVEN if all(assertions.values()) else ExperimentResult.FAILED
    return AttributionExperimentProof(
        AttributionExperimentId.BASELINE,
        run_id,
        result,
        provenance,
        source_boundary,
        metrics,
        assertions,
        evidence_ids,
        (),
        (
            "Use exact producer/native-session authority and a common latest-version "
            "population for P7/P8."
        ),
    )

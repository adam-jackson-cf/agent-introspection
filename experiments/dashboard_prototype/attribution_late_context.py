"""Pure E-Attribution-5 late-context transition accounting."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType

from experiments.dashboard_prototype.attribution_common import (
    AttributionExperimentId,
    AttributionExperimentProof,
)
from experiments.dashboard_prototype.contracts import EvidenceProvenance, ExperimentResult

_SUPPORTED_PRODUCERS = frozenset({"omp", "codex-cli", "codex-app-server"})


@dataclass(frozen=True, slots=True)
class ActivityVersion:
    """One immutable canonical activity version, including only attribution-safe fields."""

    activity_id: str
    version: int
    event_id: str
    producer: str
    surface: str
    source_time_ns: int
    attribution_state: str
    attribution_method: str
    reason_code: str | None
    project_id: str | None

    def __post_init__(self) -> None:
        if (
            not self.activity_id
            or self.version < 1
            or not self.event_id
            or self.producer not in _SUPPORTED_PRODUCERS
            or not self.surface
            or self.attribution_state not in {"resolved", "unresolved"}
            or not self.attribution_method
            or type(self.source_time_ns) is not int
            or self.source_time_ns < 0
        ):
            raise ValueError("late-context activity version is invalid")
        if self.attribution_state == "resolved" and not self.project_id:
            raise ValueError("resolved activity version requires a project ID")
        if self.attribution_state == "unresolved":
            if self.project_id is not None:
                raise ValueError("unresolved activity version cannot have a project ID")
            if not self.reason_code:
                raise ValueError("unresolved activity version requires a rejection reason")

    @property
    def activity_hash(self) -> str:
        return _hash(("activity", self.activity_id))

    @property
    def event_hash(self) -> str:
        return _hash(("event", self.event_id))


@dataclass(frozen=True, slots=True)
class LateContextTransition:
    """A valid immediate unresolved-to-resolved version transition."""

    activity_hash: str
    resolved_event_id: str
    source_time_ns: int
    producer: str
    surface: str
    attribution_method: str
    project_id: str
    prior_reason_code: str | None


@dataclass(frozen=True, slots=True)
class LateContextReduction:
    """Population-conserving local accounting for the late-context experiment."""

    transitions: tuple[LateContextTransition, ...]
    candidate_count: int
    ever_unresolved_count: int
    invalid_activity_count: int

    @property
    def transition_count(self) -> int:
        return len(self.transitions)


def reduce_late_context(
    versions: Iterable[ActivityVersion],
    *,
    start: datetime,
    end: datetime,
) -> LateContextReduction:
    """Reduce globally complete histories for immutable-source-time candidates.

    Candidates are selected by ``start < source_time <= end``.  Every selected
    stable identity is validated across its entire supplied history; only its
    unsuperseded latest version can contribute one immediate transition.
    """
    if not _ordered_window(start, end):
        raise ValueError("late-context source window must be ordered and timezone-aware")
    start_ns, end_ns = _epoch_ns(start), _epoch_ns(end)
    histories: dict[str, list[ActivityVersion]] = defaultdict(list)
    for version in versions:
        histories[version.activity_id].append(version)

    candidates = {
        activity_id
        for activity_id, history in histories.items()
        if any(start_ns < item.source_time_ns <= end_ns for item in history)
    }
    transitions: list[LateContextTransition] = []
    ever_unresolved = 0
    invalid = 0
    seen_event_ids: set[str] = set()
    for activity_id in sorted(candidates):
        history = sorted(histories[activity_id], key=lambda item: item.version)
        if not _valid_history(history, seen_event_ids):
            invalid += 1
            continue
        if any(item.attribution_state == "unresolved" for item in history):
            ever_unresolved += 1
        latest = history[-1]
        predecessor = history[-2] if len(history) > 1 else None
        if (
            latest.attribution_state == "resolved"
            and predecessor is not None
            and predecessor.attribution_state == "unresolved"
        ):
            transitions.append(
                LateContextTransition(
                    activity_hash=latest.activity_hash,
                    resolved_event_id=latest.event_hash,
                    source_time_ns=latest.source_time_ns,
                    producer=latest.producer,
                    surface=latest.surface,
                    attribution_method=latest.attribution_method,
                    project_id=latest.project_id or "",
                    prior_reason_code=predecessor.reason_code,
                )
            )
    return LateContextReduction(tuple(transitions), len(candidates), ever_unresolved, invalid)


def build_late_context_proof(
    run_id: str,
    provenance: EvidenceProvenance,
    source_boundary: str,
    reduction: LateContextReduction,
    *,
    remote_denominator: int | None,
) -> AttributionExperimentProof:
    """Build the proof; rate authority is deliberately independent of transitions."""
    invalid = reduction.invalid_activity_count > 0
    denominator_reconciled = remote_denominator == reduction.ever_unresolved_count
    blocked = []
    if remote_denominator is None:
        blocked.append("late-context.remote-ever-unresolved-denominator")
    elif not denominator_reconciled:
        blocked.append("late-context.remote-ever-unresolved-denominator-mismatch")
    if provenance is not EvidenceProvenance.FRESH_REAL:
        blocked.append("fresh-real-provenance")
    result = (
        ExperimentResult.FAILED
        if invalid
        else (ExperimentResult.PROVEN if not blocked else ExperimentResult.BLOCKED)
    )
    return AttributionExperimentProof(
        experiment_id=AttributionExperimentId.LATE_CONTEXT,
        run_id=run_id,
        result=result,
        provenance=provenance,
        source_boundary=source_boundary,
        metrics=MappingProxyType(
            {
                "candidate_count": reduction.candidate_count,
                "transition_count": reduction.transition_count,
                "ever_unresolved_count": reduction.ever_unresolved_count,
                "late_context_rate": (
                    reduction.transition_count / reduction.ever_unresolved_count
                    if denominator_reconciled and reduction.ever_unresolved_count
                    else 0.0
                ),
            }
        ),
        assertions=MappingProxyType(
            {
                "globally_valid_histories": not invalid,
                "immediate_predecessor": not invalid,
                "transition_count_independent": True,
                "denominator_reconciled": denominator_reconciled,
            }
        ),
        evidence_ids=tuple(item.resolved_event_id for item in reduction.transitions),
        blocked_boundaries=tuple(blocked),
        proposal="Count only unsuperseded immediate unresolved-to-resolved activity versions.",
    )


def _valid_history(history: list[ActivityVersion], seen_event_ids: set[str]) -> bool:
    if not history or history[0].version != 1:
        return False
    if any(item.version != expected for expected, item in enumerate(history, start=1)):
        return False
    if any(item.event_id in seen_event_ids for item in history):
        return False
    seen_event_ids.update(item.event_id for item in history)
    first = history[0]
    return all(
        item.producer == first.producer
        and item.surface == first.surface
        and item.source_time_ns == first.source_time_ns
        for item in history
    )


def _ordered_window(start: datetime, end: datetime) -> bool:
    return (
        start.tzinfo is not None
        and start.utcoffset() is not None
        and end.tzinfo is not None
        and end.utcoffset() is not None
        and start < end
    )


def _epoch_ns(value: datetime) -> int:
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


def _hash(parts: tuple[str, str]) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]

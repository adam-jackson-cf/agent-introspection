"""Pure E-Recurrence-4 intervention selection and M15 effect reducer."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType

_SAFE_ID = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z", re.ASCII)
_SUPPORTED_PRODUCERS = frozenset({"omp", "codex-cli", "codex-app-server"})


class EnforcementTier(StrEnum):
    ESTABLISHED_PROJECT_TOOL = "established_project_tool"
    NEW_TOOL = "new_tool"
    BESPOKE_SCRIPT = "bespoke_script"
    EXISTING_SKILL = "existing_skill"
    NEW_SKILL = "new_skill"
    GUIDANCE = "guidance"


ORDERED_TIERS = tuple(EnforcementTier)


class ApprovalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApplicationState(StrEnum):
    NOT_APPLIED = "not_applied"
    APPLIED = "applied"


@dataclass(frozen=True, slots=True)
class VersionedIdentity:
    """An immutable, privacy-safe identity for a finding or practice."""

    fingerprint: str
    version: int

    def __post_init__(self) -> None:
        if not _SAFE_ID.fullmatch(self.fingerprint):
            raise ValueError("identity fingerprint must be a bounded safe taxonomy")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("identity version must be a positive integer")


@dataclass(frozen=True, slots=True)
class InterventionSelection:
    """A single tier choice with auditable rejection of all stronger choices."""

    finding: VersionedIdentity
    practice: VersionedIdentity
    selected_tier: EnforcementTier
    rejection_reasons: Mapping[EnforcementTier, str]
    approval_state: ApprovalState
    application_state: ApplicationState
    applied_at: datetime | None

    def __post_init__(self) -> None:
        selected_index = ORDERED_TIERS.index(self.selected_tier)
        required = set(ORDERED_TIERS[:selected_index])
        if set(self.rejection_reasons) != required:
            raise ValueError("each earlier enforcement tier requires one explicit rejection reason")
        for tier, reason in self.rejection_reasons.items():
            if (
                not isinstance(tier, EnforcementTier)
                or not isinstance(reason, str)
                or not reason.strip()
            ):
                raise ValueError("rejection reasons must be explicit nonempty text")
        if self.application_state is ApplicationState.APPLIED:
            if self.approval_state is not ApprovalState.APPROVED or self.applied_at is None:
                raise ValueError("applied intervention requires approval and application time")
        elif self.applied_at is not None:
            raise ValueError("unapplied intervention cannot have an application time")
        if self.applied_at is not None and (
            self.applied_at.tzinfo is None or self.applied_at.utcoffset() is None
        ):
            raise ValueError("application time must be timezone-aware")
        object.__setattr__(
            self, "rejection_reasons", MappingProxyType(dict(self.rejection_reasons))
        )


@dataclass(frozen=True, slots=True)
class ObservationWindow:
    """A bounded occurrence population with its exact comparable exposure identity."""

    start: datetime
    end: datetime
    occurrences: int
    tasks: int
    evidence_fingerprint: str
    project_fingerprint: str
    producer: str
    model: str
    task_class: str

    def __post_init__(self) -> None:
        if (
            self.start.tzinfo is None
            or self.start.utcoffset() is None
            or self.end.tzinfo is None
            or self.end.utcoffset() is None
            or self.start >= self.end
        ):
            raise ValueError("observation window must be ordered timezone-aware")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (self.occurrences, self.tasks)
        ):
            raise ValueError("occurrences and tasks must be nonnegative integers")
        if not all(
            _SAFE_ID.fullmatch(value)
            for value in (
                self.evidence_fingerprint,
                self.project_fingerprint,
                self.model,
                self.task_class,
            )
        ):
            raise ValueError("exposure identities must be bounded safe taxonomies")
        if self.producer not in _SUPPORTED_PRODUCERS:
            raise ValueError("producer must be an exact supported producer")

    @property
    def duration_seconds(self) -> float:
        return (self.end - self.start).total_seconds()

    @property
    def exposure_identity(self) -> tuple[str, str, str, str, str]:
        return (
            self.evidence_fingerprint,
            self.project_fingerprint,
            self.producer,
            self.model,
            self.task_class,
        )


@dataclass(frozen=True, slots=True)
class M15Result:
    """Non-causal pre/post comparison retaining all lineage and audit boundaries."""

    finding: VersionedIdentity
    practice: VersionedIdentity
    evidence_fingerprint: str
    project_fingerprint: str
    producer: str
    model: str
    task_class: str
    approval_state: ApprovalState
    application_state: ApplicationState
    applied_at: datetime | None
    pre_start: datetime
    pre_end: datetime
    post_start: datetime
    post_end: datetime
    pre_occurrences: int
    post_occurrences: int
    pre_tasks: int
    post_tasks: int
    state: str
    comparable: bool
    pre_rate: float | None
    post_rate: float | None
    relative_change: float | None
    suppression_reason: str | None


def transition_selection(
    selection: InterventionSelection,
    approval: ApprovalState,
    application: ApplicationState,
    applied_at: datetime | None,
) -> InterventionSelection:
    """Apply the only permitted approval/application state transitions."""
    if (
        selection.approval_state is ApprovalState.REJECTED
        and approval is not ApprovalState.REJECTED
    ):
        raise ValueError("rejected approval is terminal")
    if (
        selection.approval_state is ApprovalState.APPROVED
        and approval is not ApprovalState.APPROVED
    ):
        raise ValueError("approved state is terminal")
    if (
        selection.application_state is ApplicationState.APPLIED
        and application is not ApplicationState.APPLIED
    ):
        raise ValueError("applied intervention is terminal")
    if application is ApplicationState.APPLIED and approval is not ApprovalState.APPROVED:
        raise ValueError("application requires approved state")
    return InterventionSelection(
        selection.finding,
        selection.practice,
        selection.selected_tier,
        selection.rejection_reasons,
        approval,
        application,
        applied_at,
    )


@dataclass(frozen=True, slots=True)
class _Outcome:
    state: str
    comparable: bool
    pre_rate: float | None
    post_rate: float | None
    relative_change: float | None
    suppression_reason: str | None


def reduce_m15(
    selection: InterventionSelection, pre: ObservationWindow, post: ObservationWindow
) -> M15Result:
    """Reduce exact comparable windows without claiming intervention caused a change."""
    outcome = _Outcome("suppressed", False, None, None, None, "not_applied")
    if selection.application_state is ApplicationState.APPLIED:
        if pre.end != selection.applied_at:
            outcome = _Outcome(
                "suppressed", False, None, None, None, "pre_application_boundary_mismatch"
            )
        elif post.start != selection.applied_at:
            outcome = _Outcome(
                "suppressed", False, None, None, None, "post_application_boundary_mismatch"
            )
        elif (
            pre.evidence_fingerprint != selection.finding.fingerprint
            or post.evidence_fingerprint != selection.finding.fingerprint
        ):
            outcome = _Outcome("suppressed", False, None, None, None, "finding_evidence_mismatch")
        elif pre.duration_seconds != post.duration_seconds:
            outcome = _Outcome("suppressed", False, None, None, None, "window_duration_mismatch")
        elif pre.exposure_identity != post.exposure_identity:
            outcome = _Outcome("suppressed", False, None, None, None, "exposure_identity_mismatch")
        elif pre.tasks != post.tasks:
            outcome = _Outcome("suppressed", False, None, None, None, "task_exposure_mismatch")
        elif pre.tasks == 0:
            outcome = _Outcome("suppressed", False, None, None, None, "zero_task_denominator")
        else:
            pre_rate = pre.occurrences / pre.tasks
            post_rate = post.occurrences / post.tasks
            if pre_rate == 0:
                outcome = _Outcome(
                    "pre_zero",
                    True,
                    pre_rate,
                    post_rate,
                    None,
                    "pre_zero_noncalculable",
                )
            else:
                outcome = _Outcome(
                    "calculated",
                    True,
                    pre_rate,
                    post_rate,
                    (post_rate - pre_rate) / pre_rate,
                    None,
                )
    return _result(selection, pre, post, outcome)


def _result(
    selection: InterventionSelection,
    pre: ObservationWindow,
    post: ObservationWindow,
    outcome: _Outcome,
) -> M15Result:
    return M15Result(
        selection.finding,
        selection.practice,
        pre.evidence_fingerprint,
        pre.project_fingerprint,
        pre.producer,
        pre.model,
        pre.task_class,
        selection.approval_state,
        selection.application_state,
        selection.applied_at,
        pre.start,
        pre.end,
        post.start,
        post.end,
        pre.occurrences,
        post.occurrences,
        pre.tasks,
        post.tasks,
        outcome.state,
        outcome.comparable,
        outcome.pre_rate,
        outcome.post_rate,
        outcome.relative_change,
        outcome.suppression_reason,
    )

"""Canonical deterministic-first intervention selection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class InterventionType(StrEnum):
    ESTABLISHED_TOOL = "established_tool"
    NEW_TOOL = "new_tool"
    BESPOKE_SCRIPT = "bespoke_script"
    IMPROVE_SKILL = "improve_skill"
    CREATE_SKILL = "create_skill"
    AGENTS_GUIDANCE = "agents_guidance"


@dataclass(frozen=True, slots=True)
class EnforcementTier:
    label: str
    can_enforce: bool
    reason_unavailable: str | None

    def __post_init__(self) -> None:
        if not self.can_enforce and not self.reason_unavailable:
            raise ValueError(f"{self.label} requires a reason when unavailable")
        if self.can_enforce and self.reason_unavailable is not None:
            raise ValueError(f"{self.label} cannot be both available and unavailable")


@dataclass(frozen=True, slots=True)
class InterventionDecision:
    intervention_type: InterventionType
    guidance_scope: str | None
    rationale: str


CANONICAL_TIER_LABELS = (
    "Established tools of a project first.",
    "New tools second.",
    "Bespoke scripts third.",
)


def select_intervention(
    tiers: tuple[EnforcementTier, EnforcementTier, EnforcementTier],
    *,
    workflow_owner_skill: str | None,
    repeated_ordered_workflow: bool,
    recurrence_scope: str,
) -> InterventionDecision:
    """Return exactly one intervention using the canonical tier order."""
    labels = tuple(tier.label for tier in tiers)
    if labels != CANONICAL_TIER_LABELS:
        raise ValueError("enforcement tiers are not in canonical order")
    deterministic = (
        InterventionType.ESTABLISHED_TOOL,
        InterventionType.NEW_TOOL,
        InterventionType.BESPOKE_SCRIPT,
    )
    for tier, intervention_type in zip(tiers, deterministic, strict=True):
        if tier.can_enforce:
            return InterventionDecision(
                intervention_type=intervention_type,
                guidance_scope=None,
                rationale=f"{tier.label} can represent the behavior deterministically",
            )
    if workflow_owner_skill is not None:
        return InterventionDecision(
            intervention_type=InterventionType.IMPROVE_SKILL,
            guidance_scope=workflow_owner_skill,
            rationale="the owning workflow skill must be improved through $create-skill",
        )
    if repeated_ordered_workflow:
        return InterventionDecision(
            intervention_type=InterventionType.CREATE_SKILL,
            guidance_scope=None,
            rationale="the repeated ordered workflow has no owner and requires $create-skill",
        )
    if recurrence_scope not in {"folder", "project", "cross-project"}:
        raise ValueError("recurrence scope must be folder, project or cross-project")
    target = {
        "folder": "folder guidance",
        "project": "project guidance",
        "cross-project": "~/.codex/AGENTS.md",
    }[recurrence_scope]
    return InterventionDecision(
        intervention_type=InterventionType.AGENTS_GUIDANCE,
        guidance_scope=target,
        rationale=f"cross-workflow behavior belongs in {target}",
    )

import pytest

from agent_introspection.interventions import (
    CANONICAL_TIER_LABELS,
    EnforcementTier,
    InterventionType,
    select_intervention,
)


def tiers(available: int | None = None) -> tuple[EnforcementTier, EnforcementTier, EnforcementTier]:
    values = tuple(
        EnforcementTier(
            label=label,
            can_enforce=index == available,
            reason_unavailable=None if index == available else "cannot represent the concept",
        )
        for index, label in enumerate(CANONICAL_TIER_LABELS)
    )
    return values[0], values[1], values[2]


@pytest.mark.parametrize(
    ("available", "expected"),
    [
        (0, InterventionType.ESTABLISHED_TOOL),
        (1, InterventionType.NEW_TOOL),
        (2, InterventionType.BESPOKE_SCRIPT),
    ],
)
def test_deterministic_enforcement_selects_the_first_available_tier(
    available: int, expected: InterventionType
) -> None:
    decision = select_intervention(
        tiers(available),
        workflow_owner_skill="owner",
        repeated_ordered_workflow=True,
        recurrence_scope="cross-project",
    )
    assert decision.intervention_type is expected


def test_nondeterministic_behavior_selects_owner_then_new_skill_then_guidance() -> None:
    owner = select_intervention(
        tiers(),
        workflow_owner_skill="workflow-owner",
        repeated_ordered_workflow=True,
        recurrence_scope="project",
    )
    new_skill = select_intervention(
        tiers(),
        workflow_owner_skill=None,
        repeated_ordered_workflow=True,
        recurrence_scope="project",
    )
    guidance = select_intervention(
        tiers(),
        workflow_owner_skill=None,
        repeated_ordered_workflow=False,
        recurrence_scope="cross-project",
    )
    assert owner.intervention_type is InterventionType.IMPROVE_SKILL
    assert new_skill.intervention_type is InterventionType.CREATE_SKILL
    assert guidance.intervention_type is InterventionType.AGENTS_GUIDANCE
    assert guidance.guidance_scope == "~/.codex/AGENTS.md"


def test_unavailable_tiers_require_reasons_and_canonical_order() -> None:
    with pytest.raises(ValueError, match="requires a reason"):
        EnforcementTier(CANONICAL_TIER_LABELS[0], False, None)
    wrong = (
        EnforcementTier(CANONICAL_TIER_LABELS[1], False, "no"),
        EnforcementTier(CANONICAL_TIER_LABELS[0], False, "no"),
        EnforcementTier(CANONICAL_TIER_LABELS[2], False, "no"),
    )
    with pytest.raises(ValueError, match="canonical order"):
        select_intervention(
            wrong,
            workflow_owner_skill=None,
            repeated_ordered_workflow=False,
            recurrence_scope="folder",
        )

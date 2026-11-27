from __future__ import annotations

from enum import StrEnum


class InterventionType(StrEnum):
    ESTABLISHED_TOOL = "established_tool"
    NEW_TOOL = "new_tool"
    BESPOKE_SCRIPT = "bespoke_script"
    IMPROVE_SKILL = "improve_skill"
    CREATE_SKILL = "create_skill"
    AGENTS_GUIDANCE = "agents_guidance"


CANONICAL_TIER_LABELS = (
    "Established tools of a project first.",
    "New tools second.",
    "Bespoke scripts third.",
)

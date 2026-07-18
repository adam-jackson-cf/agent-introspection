from __future__ import annotations

import pytest

from agent_introspection.outcomes import derive_outcome


@pytest.mark.parametrize(
    ("event_name", "decision_source", "decision", "hydrated_outcome", "expected"),
    [
        (
            "codex.tool_decision",
            "user",
            "continue",
            "ignored",
            ("turn/steer", "continue"),
        ),
        (
            "codex.tool_decision",
            "system",
            "continue",
            "completed",
            ("codex.tool_decision", "completed"),
        ),
        ("codex.tool_result", None, None, None, ("codex.tool_result", None)),
    ],
)
def test_derive_outcome_uses_only_the_canonical_user_decision_rule(
    event_name: str,
    decision_source: str | None,
    decision: str | None,
    hydrated_outcome: str | None,
    expected: tuple[str, str | None],
) -> None:
    assert (
        derive_outcome(
            event_name=event_name,
            decision_source=decision_source,
            decision=decision,
            hydrated_outcome=hydrated_outcome,
        )
        == expected
    )

"""Canonical outcome derivation for detector input."""

from __future__ import annotations


def derive_outcome(
    *,
    event_name: str,
    decision_source: str | None,
    decision: str | None,
    hydrated_outcome: str | None,
) -> tuple[str, str | None]:
    """Return the detector event name and outcome from allowlisted source facts."""

    if event_name == "codex.tool_decision" and decision_source == "user":
        return "turn/steer", decision
    return event_name, hydrated_outcome

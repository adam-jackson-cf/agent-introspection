"""Render the generated block of the producer capabilities reference.

Used by ``scripts/render-measures.py [--check]``. The block lists each capability from
the signal registry with what to look for, how to check it, its hints, and the signals
that require it, then the status every harness profile claims. Tables are padded the
way Prettier formats Markdown, so a regenerated document stays clean.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from agent_introspection import capabilities, facts, profiles
from agent_introspection.capabilities import CapabilityClaim

CAPABILITIES_DOC = (
    Path(__file__).resolve().parents[2] / "docs" / "references" / "producer-capabilities.md"
)
_HINT_LABELS = {
    "span_names": "Span names",
    "event_names": "Event names",
    "attribute_keys": "Attribute keys",
}


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    """Return a Markdown table with every column padded to its widest cell."""
    cells = [[cell.replace("|", "\\|") for cell in row] for row in [header, *rows]]
    widths = [max(3, *(len(row[index]) for row in cells)) for index in range(len(header))]
    lines = [cells[0], ["-" * width for width in widths], *cells[1:]]
    return [
        "| " + " | ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)) + " |"
        for row in lines
    ]


def _patterns(patterns: Sequence[str]) -> str:
    return ", ".join(f"`{pattern}`" for pattern in patterns) or "none"


def _required_by(registry: facts.Registry, capability: str) -> list[str]:
    """Return one list item per view naming the signals there that require ``capability``."""
    by_view: dict[str, list[str]] = {}
    for signal in registry.signals:
        if capability in registry.requires.get(str(signal["signal"]), ()):
            by_view.setdefault(str(signal["view_title"]), []).append(
                f"{signal['title']} (`{signal['signal']}`)"
            )
    return [f"  - {view}: {', '.join(titles)}" for view, titles in by_view.items()]


def _capability_section(registry: facts.Registry, capability: capabilities.Capability) -> list[str]:
    return [
        f"### {capability.title} (`{capability.id}`)",
        "",
        f"- **Need:** {capability.need}",
        f"- **Check:** {capability.check}",
        *(
            f"- **{label}:** {_patterns(capability.hints[kind])}"
            for kind, label in _HINT_LABELS.items()
        ),
        "- **Required by:**",
        *_required_by(registry, capability.id),
        "",
    ]


def _column(profile: profiles.Profile) -> str:
    return profile.title if profile.status == "registered" else f"{profile.title} (candidate)"


def _status_matrix(loaded: Sequence[profiles.Profile]) -> list[str]:
    rows = [
        [f"`{capability}`", *(profile.capabilities[capability].status for profile in loaded)]
        for capability in capabilities.CAPABILITY_IDS
    ]
    return _table(["Capability", *(_column(profile) for profile in loaded)], rows)


def _exceptional_claims(
    profile: profiles.Profile, labels: dict[str, str]
) -> list[tuple[str, str, CapabilityClaim]]:
    """Return the profile's claims that lack some signals, then its surface overrides."""
    lacking = [
        (profile.title, capability, claim)
        for capability, claim in profile.capabilities.items()
        if claim.lacks
    ]
    overridden = [
        (labels[surface], capability, claim)
        for surface, overrides in profile.capability_overrides.items()
        for capability, claim in overrides.items()
    ]
    return [*lacking, *overridden]


def _exceptions(registry: facts.Registry, loaded: Sequence[profiles.Profile]) -> list[str]:
    """Return a table of the claims that lack some signals or that a surface overrides."""
    titles = {str(signal["signal"]): str(signal["title"]) for signal in registry.signals}
    claims = [
        claim for profile in loaded for claim in _exceptional_claims(profile, registry.harnesses)
    ]
    rows = [
        [
            producer,
            f"`{capability}`",
            ", ".join(titles[signal] for signal in claim.lacks) or "every signal",
            "missing" if claim.lacks else claim.status,
            claim.evidence,
        ]
        for producer, capability, claim in claims
    ]
    return _table(["Producer", "Capability", "Signals", "Status", "Evidence"], rows)


def render_capabilities(
    registry: facts.Registry | None = None, loaded: Sequence[profiles.Profile] | None = None
) -> str:
    """Return the generated block: capabilities, then the status each profile claims."""
    registry = registry or facts.load_registry()
    loaded = profiles.load_profiles(registry) if loaded is None else loaded
    lines = ["## Capabilities", ""]
    for capability in capabilities.load_capabilities():
        lines += _capability_section(registry, capability)
    lines += [
        "## Status by producer",
        "",
        "The status each harness profile claims for the producer as a whole. A",
        "registered profile's claims agree with every registry cell of its harnesses.",
        "",
        *_status_matrix(loaded),
        "",
        "### Exceptions",
        "",
        "Where a claim differs for some signals or for one surface: a capability the",
        "producer has is missing for the signals named, and an override sets the claim",
        "for every signal of one surface.",
        "",
        *_exceptions(registry, loaded),
    ]
    return "\n".join(lines) + "\n"

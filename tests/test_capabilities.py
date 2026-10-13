from __future__ import annotations

import copy
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from agent_introspection import capabilities, facts, profiles
from agent_introspection.capabilities import CapabilityClaim


@dataclass(frozen=True)
class _Profile:
    """A minimal capability profile: one status per capability, with overrides."""

    key: str
    statuses: dict[str, str]
    registry_keys: tuple[str, ...] = ()
    lacks: dict[str, tuple[str, ...]] = field(default_factory=dict)
    overrides: dict[str, dict[str, str]] = field(default_factory=dict)

    def claims(self, registry_key: str = "") -> dict[str, CapabilityClaim]:
        statuses = {**self.statuses, **self.overrides.get(registry_key, {})}
        return {
            capability: CapabilityClaim(status, "e", lacks=self.lacks.get(capability, ()))
            for capability, status in statuses.items()
        }


def _all(status: str, **changes: str) -> dict[str, str]:
    return {
        capability: changes.get(capability, status) for capability in capabilities.CAPABILITY_IDS
    }


def _capability_entry(capability: str) -> dict[str, Any]:
    return {
        "id": capability,
        "title": capability,
        "need": "n",
        "check": "c",
        "hints": {"span_names": [], "event_names": [], "attribute_keys": [f"^{capability}$"]},
    }


def _signal(signal: str, requires: list[str], **support: dict[str, str]) -> dict[str, Any]:
    return {
        "id": signal,
        "view": "v",
        "title": signal.title(),
        "question": "q",
        "unit": "u",
        "formula": "f",
        "requires": requires,
        "support": support,
    }


def _document() -> dict[str, Any]:
    route = {"source": "spans", "match": "1", "description": "d"}
    aligned = {"alignment": "aligned"}
    return {
        "harnesses": {"alpha": "Alpha", "beta": "Beta"},
        "views": {"v": "View"},
        "capability": [_capability_entry(capability) for capability in capabilities.CAPABILITY_IDS],
        "route": [
            {"id": "alpha.r", "harnesses": ["alpha"], **route},
            {"id": "beta.r", "harnesses": ["beta"], **route},
        ],
        "signal": [
            _signal(
                "tokens",
                ["model_usage"],
                alpha={"route": "alpha.r", **aligned},
                beta={"route": "beta.r", **aligned},
            ),
            _signal(
                "steps",
                ["task_boundary", "model_outcome"],
                alpha={"route": "alpha.r", **aligned},
                beta={"alignment": "not emitted", "note": "beta has no model calls"},
            ),
            _signal(
                "interrupts",
                ["task_boundary", "user_friction"],
                alpha={"route": "alpha.r", **aligned},
                beta={"alignment": "not applicable", "note": "headless"},
            ),
            {
                "id": "loader",
                "view": "v",
                "title": "Loader",
                "question": "q",
                "unit": "u",
                "formula": "f",
                "scope": "system",
            },
        ],
    }


def _registry() -> facts.Registry:
    return facts.parse_registry(_document())


def test_repo_registry_declares_every_capability_and_requires_one_per_harness_signal() -> None:
    registry = facts.load_registry()
    declared = capabilities.load_capabilities()

    assert [capability.id for capability in declared] == list(capabilities.CAPABILITY_IDS)
    harness_signals = [str(row["signal"]) for row in registry.signals if row["scope"] == "harness"]
    assert list(registry.requires) == harness_signals
    assert all(registry.requires[signal] for signal in harness_signals)
    # Every capability is consumed by some signal, so none is declared for nothing.
    used = {capability for required in registry.requires.values() for capability in required}
    assert used == set(capabilities.CAPABILITY_IDS)


@pytest.mark.parametrize(
    ("kind", "name", "capability"),
    [
        ("attribute_keys", "gen_ai.usage.input_tokens", "model_usage"),
        ("attribute_keys", "input_token_count", "model_usage"),
        ("attribute_keys", "reasoning_token_count", "reasoning_usage"),
        ("attribute_keys", "Session.ID", "session_identity"),
        ("attribute_keys", "gen_ai.conversation.id", "session_identity"),
        ("attribute_keys", "tool.call_id", "tool_calls"),
        ("attribute_keys", "gen_ai.response.model", "model_identity"),
        ("attribute_keys", "ttft_ms", "model_outcome"),
        ("span_names", "session_task.turn", "task_boundary"),
        ("span_names", "claude_code.interaction", "task_boundary"),
        ("span_names", "turn/interrupt", "user_friction"),
        ("span_names", "invoke_agent Explore", "delegation"),
        ("span_names", "Permission.ask", "approvals"),
        ("event_names", "codex.tool_decision", "approvals"),
        ("event_names", "tool.execute.before", "tool_arguments"),
        ("event_names", "user_prompt", "prompt_text"),
    ],
)
def test_hints_recognise_known_producer_names_ignoring_case(
    kind: str, name: str, capability: str
) -> None:
    by_id = {item.id: item for item in capabilities.load_capabilities()}

    assert by_id[capability].matches(kind, name)
    assert by_id[capability].matches(kind, name.upper())


def test_hints_do_not_claim_unrelated_names() -> None:
    by_id = {item.id: item for item in capabilities.load_capabilities()}

    assert not by_id["session_identity"].matches("attribute_keys", "session.title")
    assert not by_id["delegation"].matches("span_names", "invoke_agent")
    assert not by_id["session_store"].matches("attribute_keys", "cwd")


def _mutate_capability(document: dict[str, Any], **changes: Any) -> None:
    document["capability"][3].update(changes)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: _mutate_capability(d, id="tokens"), "unknown id"),
        (lambda d: _mutate_capability(d, id="export"), "duplicate id export"),
        (lambda d: d["capability"].pop(), "session_store not declared"),
        (lambda d: _mutate_capability(d, need=""), "need must be text"),
        (lambda d: _mutate_capability(d, owner="x"), "unknown key owner"),
        (lambda d: _mutate_capability(d, hints={"span_names": []}), "hints must be a table"),
        (
            lambda d: _mutate_capability(
                d, hints={"span_names": ["(unclosed"], "event_names": [], "attribute_keys": []}
            ),
            "not a regex",
        ),
        (
            lambda d: _mutate_capability(
                d, hints={"span_names": "chat", "event_names": [], "attribute_keys": []}
            ),
            "hints.span_names must be a list",
        ),
        (lambda d: d.update(capability={}), "array of tables"),
        (lambda d: d["capability"].append("export"), "must be a table"),
        (lambda d: d["signal"][0].pop("requires"), "tokens: requires must list"),
        (lambda d: d["signal"][0].update(requires=[]), "tokens: requires must list"),
        (lambda d: d["signal"][0].update(requires=["tokens"]), "unknown capability tokens"),
        (
            lambda d: d["signal"][0].update(requires=["model_usage", "model_usage"]),
            "repeats a capability",
        ),
        (lambda d: d["signal"][3].update(requires=["export"]), "system signal has no requires"),
    ],
)
def test_registry_rejects_invalid_capabilities_or_requirements(
    mutate: Callable[[dict[str, Any]], object], message: str
) -> None:
    document = _document()
    mutate(document)

    with pytest.raises(facts.RegistryError, match=message):
        facts.parse_registry(document)


def test_facts_install_rejects_a_repo_registry_signal_without_requirements() -> None:
    document = tomllib.loads(facts._sql(facts.REGISTRY_FILE))
    broken = copy.deepcopy(document)
    next(entry for entry in broken["signal"] if "requires" in entry).pop("requires")

    assert facts.parse_registry(document).requires
    with pytest.raises(facts.RegistryError, match="requires must list"):
        facts.parse_registry(broken)


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["native", "hook"], capabilities.REACHABLE),
        (["native", "hook_available"], capabilities.REACHABLE_WITH_WORK),
        (["hook_available", "unverified"], capabilities.UNKNOWN),
        (["unverified", "missing"], capabilities.UNREACHABLE),
        (["missing", "not_applicable"], capabilities.NOT_APPLICABLE),
    ],
)
def test_outcome_takes_the_weakest_required_status(statuses: list[str], expected: str) -> None:
    assert capabilities.outcome(statuses) == expected


def test_project_maps_claims_onto_each_harness_signal_with_overrides_and_lacks() -> None:
    registry = _registry()
    profile = _Profile(
        key="gamma",
        statuses=_all("native", model_outcome="hook_available", user_friction="hook"),
        registry_keys=("alpha", "beta"),
        lacks={"model_usage": ("tokens",)},
        overrides={"beta": {"user_friction": "not_applicable"}},
    )

    assert capabilities.project(profile, registry) == {
        "tokens": capabilities.UNREACHABLE,
        "steps": capabilities.REACHABLE_WITH_WORK,
        "interrupts": capabilities.REACHABLE,
    }
    assert capabilities.project(profile, registry, "beta")["interrupts"] == (
        capabilities.NOT_APPLICABLE
    )
    unclaimed = _Profile(key="delta", statuses={"model_usage": "native"})
    assert capabilities.project(unclaimed, registry)["steps"] == capabilities.UNKNOWN


def test_impact_hides_what_a_new_producer_cannot_reach_today() -> None:
    registry = _registry()
    candidate = _Profile(
        key="gamma",
        statuses=_all("native", model_usage="hook_available", user_friction="missing"),
    )

    result = capabilities.impact(["alpha"], candidate, registry)

    assert result["enabled"] == ["alpha"]
    assert result["visible_now"] == ["tokens", "steps", "interrupts"]
    assert result["visible_after"] == ["steps"]
    assert result["hidden"] == [
        {
            "signal": "tokens",
            "title": "Tokens",
            "outcome": capabilities.REACHABLE_WITH_WORK,
            "missing": ["model_usage"],
            "note": "",
        },
        {
            "signal": "interrupts",
            "title": "Interrupts",
            "outcome": capabilities.UNREACHABLE,
            "missing": ["user_friction"],
            "note": "",
        },
    ]


def test_impact_judges_a_registered_surface_by_its_registry_cell() -> None:
    registry = _registry()
    beta = _Profile(key="beta", statuses=_all("native"), registry_keys=("beta",))

    result = capabilities.impact(["alpha"], beta, registry)

    # The cell is authoritative: steps is not emitted for beta, even though the
    # profile claims every capability; not applicable keeps interrupts visible.
    assert result["visible_after"] == ["tokens", "interrupts"]
    assert result["hidden"] == [
        {
            "signal": "steps",
            "title": "Steps",
            "outcome": capabilities.UNREACHABLE,
            "missing": [],
            "note": "beta has no model calls",
        }
    ]
    # Signals already hidden on this machine are not hidden again.
    already = capabilities.impact(["alpha", "beta"], beta, registry)
    assert already["visible_now"] == ["tokens", "interrupts"]
    assert already["hidden"] == []


def test_impact_of_opencode_over_claude_code_and_codex_hides_its_token_signals() -> None:
    registry = facts.load_registry()
    opencode = profiles.resolve("opencode", profiles.load_profiles(registry))

    result = capabilities.impact(["claude-code", "codex"], opencode, registry)

    hidden = {entry["signal"]: entry for entry in result["hidden"]}
    for signal in ("usage.input_tokens", "usage.cached_input", "usage.output_tokens"):
        assert hidden[signal]["outcome"] == capabilities.REACHABLE_WITH_WORK
        assert hidden[signal]["missing"] == ["model_usage"]
    assert "tools.calls" in result["visible_after"]
    # Codex already hides throughput here, so opencode cannot hide it again.
    assert "provider.throughput" not in result["visible_now"]
    assert "provider.throughput" not in hidden


def test_impact_of_codex_over_claude_code_reports_the_registry_reason() -> None:
    registry = facts.load_registry()
    codex = profiles.resolve("codex", profiles.load_profiles(registry))

    result = capabilities.impact(["claude-code"], codex, registry)

    hidden = {entry["signal"]: entry for entry in result["hidden"]}
    assert set(hidden) == {"usage.cache_creation", "provider.throughput"}
    assert hidden["usage.cache_creation"]["missing"] == ["model_usage"]
    assert hidden["provider.throughput"]["missing"] == ["model_outcome"]
    assert "cache_write_token_count" in hidden["usage.cache_creation"]["note"]

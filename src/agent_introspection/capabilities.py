from __future__ import annotations

import re
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from importlib.resources import files
from typing import Any, Protocol, TypedDict, cast

from agent_introspection import facts

CAPABILITY_IDS = (
    "export",
    "session_identity",
    "task_boundary",
    "model_usage",
    "reasoning_usage",
    "model_identity",
    "model_outcome",
    "tool_calls",
    "tool_arguments",
    "tool_failure",
    "user_friction",
    "approvals",
    "delegation",
    "prompt_text",
)
HINT_KINDS = ("span_names", "event_names", "attribute_keys")
STATUSES = ("native", "hook", "hook_available", "missing", "not_applicable", "unverified")
PRESENT = frozenset({"native", "hook"})

REACHABLE = "reachable"
REACHABLE_WITH_WORK = "reachable_with_work"
UNKNOWN = "unknown"
UNREACHABLE = "unreachable"
NOT_APPLICABLE = "not_applicable"
OUTCOMES = (REACHABLE, REACHABLE_WITH_WORK, UNKNOWN, UNREACHABLE, NOT_APPLICABLE)
_HIDING = (UNREACHABLE, UNKNOWN, REACHABLE_WITH_WORK)
_CELL_OUTCOMES = {
    "aligned": REACHABLE,
    "differs": REACHABLE,
    "not applicable": NOT_APPLICABLE,
    "not emitted": UNREACHABLE,
}
_CAPABILITY_KEYS = frozenset({"id", "title", "need", "check", "hints"})


@dataclass(frozen=True)
class Capability:
    id: str
    title: str
    need: str
    check: str
    hints: Mapping[str, tuple[str, ...]]

    def matches(self, kind: str, name: str) -> bool:
        return any(re.search(pattern, name, re.IGNORECASE) for pattern in self.hints[kind])


@dataclass(frozen=True)
class CapabilityClaim:
    status: str
    evidence: str
    route: str = ""
    lacks: tuple[str, ...] = ()


class HiddenSignal(TypedDict):
    signal: str
    title: str
    outcome: str
    missing: list[str]
    note: str


class Impact(TypedDict):
    candidate: str
    enabled: list[str]
    visible_now: list[str]
    visible_after: list[str]
    hidden: list[HiddenSignal]


class CapabilityProfile(Protocol):
    @property
    def key(self) -> str: ...

    @property
    def registry_keys(self) -> tuple[str, ...]: ...

    def claims(self, registry_key: str = "") -> Mapping[str, CapabilityClaim]: ...


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise facts.RegistryError(message)


def _text(entry: Mapping[str, Any], key: str, where: str) -> str:
    value = entry.get(key)
    _require(isinstance(value, str) and value.strip() != "", f"{where}: {key} must be text")
    return cast(str, value)


def _registry_document() -> dict[str, Any]:
    return tomllib.loads(
        files("agent_introspection.facts_sql").joinpath(facts.REGISTRY_FILE).read_text()
    )


def _pattern(pattern: object, where: str) -> str:
    _require(isinstance(pattern, str) and pattern != "", f"{where}: a hint must be text")
    try:
        re.compile(cast(str, pattern), re.IGNORECASE)
    except re.error as error:
        raise facts.RegistryError(f"{where}: hint {pattern!r} is not a regex: {error}") from None
    return cast(str, pattern)


def _hints(value: object, where: str) -> dict[str, tuple[str, ...]]:
    _require(
        isinstance(value, Mapping) and set(value) == set(HINT_KINDS),
        f"{where}: hints must be a table of {', '.join(HINT_KINDS)}",
    )
    hints: dict[str, tuple[str, ...]] = {}
    for kind in HINT_KINDS:
        patterns = cast(Mapping[str, object], value)[kind]
        _require(isinstance(patterns, list), f"{where}: hints.{kind} must be a list")
        hints[kind] = tuple(
            _pattern(pattern, f"{where} hints.{kind}") for pattern in cast(list[object], patterns)
        )
    return hints


def _capability(entry: object) -> Capability:
    _require(isinstance(entry, Mapping), "capability: each entry must be a table")
    table = cast(Mapping[str, Any], entry)
    capability = _text(table, "id", "capability")
    where = f"capability {capability}"
    _require(
        capability in CAPABILITY_IDS,
        f"{where}: unknown id; known: {', '.join(CAPABILITY_IDS)}",
    )
    unknown = sorted(set(table) - _CAPABILITY_KEYS)
    _require(not unknown, f"{where}: unknown key {', '.join(unknown)}")
    return Capability(
        id=capability,
        title=_text(table, "title", where),
        need=_text(table, "need", where),
        check=_text(table, "check", where),
        hints=_hints(table.get("hints"), where),
    )


def load_capabilities(document: Mapping[str, Any] | None = None) -> tuple[Capability, ...]:
    source = _registry_document() if document is None else document
    entries = source.get("capability", [])
    _require(isinstance(entries, list), "capability must be an array of tables")
    capabilities = [_capability(entry) for entry in entries]
    ids = [capability.id for capability in capabilities]
    repeated = sorted({capability for capability in ids if ids.count(capability) > 1})
    _require(not repeated, f"capability: duplicate id {', '.join(repeated)}")
    undeclared = [capability for capability in CAPABILITY_IDS if capability not in ids]
    _require(not undeclared, f"capability: {', '.join(undeclared)} not declared")
    return tuple(capabilities)


def _required_ids(value: object, signal: str) -> tuple[str, ...]:
    _require(
        isinstance(value, list) and bool(value),
        f"{signal}: requires must list the capabilities the signal needs",
    )
    required = cast(list[object], value)
    unknown = [str(item) for item in required if item not in CAPABILITY_IDS]
    _require(not unknown, f"{signal}: requires unknown capability {', '.join(unknown)}")
    _require(len(set(required)) == len(required), f"{signal}: requires repeats a capability")
    return tuple(cast(list[str], required))


def requirements(registry_document: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    load_capabilities(registry_document)
    required: dict[str, tuple[str, ...]] = {}
    for entry in registry_document.get("signal", []):
        signal = _text(entry, "id", "signal")
        if entry.get("scope", "harness") == "system":
            _require("requires" not in entry, f"{signal}: a system signal has no requires")
        else:
            required[signal] = _required_ids(entry.get("requires"), signal)
    return required


def statuses_for(
    claims: Mapping[str, CapabilityClaim], signal: str, required: Iterable[str]
) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for capability in required:
        claim = claims.get(capability)
        if claim is None:
            statuses[capability] = "unverified"
        else:
            statuses[capability] = "missing" if signal in claim.lacks else claim.status
    return statuses


def outcome(statuses: Iterable[str]) -> str:
    present = set(statuses)
    if "not_applicable" in present:
        return NOT_APPLICABLE
    if "missing" in present:
        return UNREACHABLE
    if "unverified" in present:
        return UNKNOWN
    if "hook_available" in present:
        return REACHABLE_WITH_WORK
    return REACHABLE


def project(
    profile: CapabilityProfile, registry: facts.Registry, registry_key: str = ""
) -> dict[str, str]:
    claims = profile.claims(registry_key)
    return {
        signal: outcome(statuses_for(claims, signal, required).values())
        for signal, required in registry.requires.items()
    }


def _blocking(statuses: Mapping[str, str]) -> list[str]:
    return [
        capability
        for capability, status in statuses.items()
        if status not in PRESENT and status != "not_applicable"
    ]


def _cells(registry: facts.Registry, signal: str) -> dict[str, facts.Row]:
    return {str(row["harness"]): row for row in registry.support if row["signal"] == signal}


def _hidden_entry(
    candidate: CapabilityProfile, registry: facts.Registry, signal: facts.Row
) -> HiddenSignal | None:
    signal_id = str(signal["signal"])
    required = registry.requires.get(signal_id, ())
    cells = _cells(registry, signal_id)
    surfaces = [key for key in candidate.registry_keys if key in cells] or [""]
    outcomes: list[str] = []
    missing: list[str] = []
    notes: list[str] = []
    for surface in surfaces:
        statuses = statuses_for(candidate.claims(surface), signal_id, required)
        cell = cells.get(surface)
        result = _CELL_OUTCOMES[str(cell["alignment"])] if cell else outcome(statuses.values())
        if result in _HIDING:
            outcomes.append(result)
            missing.extend(_blocking(statuses))
            notes.append(str(cell["note"]) if cell else "")
    if not outcomes:
        return None
    return {
        "signal": signal_id,
        "title": str(signal["title"]),
        "outcome": next(level for level in _HIDING if level in outcomes),
        "missing": [capability for capability in CAPABILITY_IDS if capability in missing],
        "note": "; ".join(dict.fromkeys(note for note in notes if note)),
    }


def impact(
    enabled: Sequence[str] | None, candidate: CapabilityProfile, registry: facts.Registry
) -> Impact:
    harnesses = facts.enabled_harnesses(registry, enabled)
    hidden_now = {str(row["signal"]) for row in facts.hidden_signals(registry, harnesses)}
    visible_now = [
        signal
        for signal in registry.signals
        if signal["scope"] == "harness" and signal["signal"] not in hidden_now
    ]
    hidden = [
        entry
        for signal in visible_now
        if (entry := _hidden_entry(candidate, registry, signal)) is not None
    ]
    newly_hidden = {entry["signal"] for entry in hidden}
    return {
        "candidate": candidate.key,
        "enabled": list(harnesses),
        "visible_now": [str(signal["signal"]) for signal in visible_now],
        "visible_after": [
            str(signal["signal"]) for signal in visible_now if signal["signal"] not in newly_hidden
        ],
        "hidden": hidden,
    }

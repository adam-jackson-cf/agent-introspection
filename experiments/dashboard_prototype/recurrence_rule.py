"""Pure fail-closed E-Recurrence-3 rule-adherence reduction."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final

from experiments.dashboard_prototype.contracts import PrototypeContractError
from experiments.dashboard_prototype.recurrence_common import SUPPORTED_SURFACES

_SAFE_TOKEN: Final[re.Pattern[str]] = re.compile(r"[a-z][a-z0-9_.:-]{0,127}\Z", re.ASCII)
RuleKey = tuple[str, str, str, str]
TaskKey = tuple[str, str, str, str, str]


class ApplicabilityState(StrEnum):
    APPLICABLE = "applicable"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


class SatisfactionState(StrEnum):
    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class VersionedRule:
    producer: str
    surface: str
    native_session_hash: str
    rule_id: str
    rule_version: str
    trigger: str
    required_action: str
    observability_condition: str
    source_time: datetime
    source_order: int
    evidence_id: str


@dataclass(frozen=True, slots=True)
class RuleApplicability:
    producer: str
    surface: str
    native_session_hash: str
    rule_id: str
    rule_version: str
    task_id: str
    state: ApplicabilityState
    source_time: datetime
    source_order: int
    evidence_id: str


@dataclass(frozen=True, slots=True)
class RuleViolation:
    producer: str
    surface: str
    native_session_hash: str
    rule_id: str
    rule_version: str
    task_id: str
    state: SatisfactionState
    observed_action: str | None
    authoritative: bool
    source_time: datetime
    source_order: int
    evidence_id: str


@dataclass(frozen=True, slots=True)
class RuleAdherenceBoundary:
    source_start: datetime
    source_end: datetime
    source_boundary: str
    rules: Iterable[VersionedRule] = ()
    applicability: Iterable[RuleApplicability] = ()
    violations: Iterable[RuleViolation] = ()


@dataclass(frozen=True, slots=True)
class RuleAdherence:
    producer: str
    surface: str
    rule_id: str
    rule_version: str
    trigger: str
    required_action: str
    observability_condition: str
    applicable_count: int
    satisfied_count: int
    unsatisfied_count: int
    unknown_count: int
    denominator: int
    numerator: int
    percentage: float | None


def reduce_rule_adherence(*, boundary: RuleAdherenceBoundary) -> tuple[RuleAdherence, ...]:
    """Reduce only the globally latest rule version; unknowns never count as failures."""
    _validate_boundary(boundary)
    rules = _latest_rules(_index_rules(boundary))
    applicability = _index_applicability(boundary, rules)
    observations = _index_observations(boundary, rules, applicability)
    reductions: list[RuleAdherence] = []
    for key, rule in sorted(rules.items()):
        states = [
            observations.get((*key, record.task_id), SatisfactionState.UNKNOWN)
            for task_key, record in applicability.items()
            if task_key[:4] == key and record.state is ApplicabilityState.APPLICABLE
        ]
        satisfied = sum(state is SatisfactionState.SATISFIED for state in states)
        unsatisfied = sum(state is SatisfactionState.UNSATISFIED for state in states)
        unknown = sum(state is SatisfactionState.UNKNOWN for state in states)
        denominator = satisfied + unsatisfied
        reductions.append(
            RuleAdherence(
                producer=rule.producer,
                surface=rule.surface,
                rule_id=rule.rule_id,
                rule_version=rule.rule_version,
                trigger=rule.trigger,
                required_action=rule.required_action,
                observability_condition=rule.observability_condition,
                applicable_count=len(states),
                satisfied_count=satisfied,
                unsatisfied_count=unsatisfied,
                unknown_count=unknown,
                denominator=denominator,
                numerator=satisfied,
                percentage=None if denominator == 0 else 100.0 * satisfied / denominator,
            )
        )
    return tuple(reductions)


def _validate_boundary(boundary: RuleAdherenceBoundary) -> None:
    _time(boundary.source_start, "source start")
    _time(boundary.source_end, "source end")
    if boundary.source_start >= boundary.source_end or not boundary.source_boundary:
        raise PrototypeContractError("rule boundary must be a positive bounded window")


def _rule_key(item: VersionedRule | RuleApplicability | RuleViolation) -> RuleKey:
    return item.producer, item.surface, item.rule_id, item.rule_version


def _task_key(item: RuleApplicability | RuleViolation) -> TaskKey:
    return (*_rule_key(item), item.task_id)


def _validate_identity(
    item: VersionedRule | RuleApplicability | RuleViolation,
    boundary: RuleAdherenceBoundary,
    *,
    require_in_window: bool = True,
) -> None:
    if (item.producer, item.surface) not in SUPPORTED_SURFACES:
        raise PrototypeContractError("rule producer lineage is unsupported")
    _hash(item.native_session_hash, "native session hash")
    for value, label in (
        (item.rule_id, "rule ID"),
        (item.rule_version, "rule version"),
        (item.evidence_id, "evidence ID"),
    ):
        _token(value, label)
    _time(item.source_time, "source time")
    if require_in_window and (
        item.source_time <= boundary.source_start or item.source_time > boundary.source_end
    ):
        raise PrototypeContractError("rule record falls outside source membership boundary")
    if (
        isinstance(item.source_order, bool)
        or not isinstance(item.source_order, int)
        or item.source_order < 0
    ):
        raise PrototypeContractError("rule source order must be nonnegative")


def _index_rules(boundary: RuleAdherenceBoundary) -> dict[RuleKey, VersionedRule]:
    result: dict[RuleKey, VersionedRule] = {}
    for item in boundary.rules:
        _validate_identity(item, boundary, require_in_window=False)
        _token(item.trigger, "trigger")
        _token(item.required_action, "required action")
        _token(item.observability_condition, "observability condition")
        key = _rule_key(item)
        previous = result.get(key)
        if previous is not None and previous != item:
            raise PrototypeContractError("conflicting durable rule records")
        result[key] = item
    return result


def _latest_rules(rules: dict[RuleKey, VersionedRule]) -> dict[RuleKey, VersionedRule]:
    latest: dict[tuple[str, str, str], VersionedRule] = {}
    for rule in rules.values():
        identity = rule.producer, rule.surface, rule.rule_id
        previous = latest.get(identity)
        if previous is None or (rule.source_time, rule.source_order) > (
            previous.source_time,
            previous.source_order,
        ):
            latest[identity] = rule
        elif (rule.source_time, rule.source_order) == (previous.source_time, previous.source_order):
            raise PrototypeContractError("globally latest rule version is ambiguous")
    return {_rule_key(rule): rule for rule in latest.values()}


def _index_applicability(
    boundary: RuleAdherenceBoundary, rules: dict[RuleKey, VersionedRule]
) -> dict[TaskKey, RuleApplicability]:
    result: dict[TaskKey, RuleApplicability] = {}
    for item in boundary.applicability:
        _validate_identity(item, boundary)
        _token(item.task_id, "task ID")
        if _rule_key(item) not in rules:
            continue
        key = _task_key(item)
        previous = result.get(key)
        if previous is not None and previous.state is not item.state:
            raise PrototypeContractError("conflicting canonical task applicability records")
        result[key] = item
    return result


def _index_observations(
    boundary: RuleAdherenceBoundary,
    rules: dict[RuleKey, VersionedRule],
    applicability: dict[TaskKey, RuleApplicability],
) -> dict[TaskKey, SatisfactionState]:
    result: dict[TaskKey, SatisfactionState] = {}
    for item in boundary.violations:
        _validate_identity(item, boundary)
        _token(item.task_id, "task ID")
        key = _task_key(item)
        rule = rules.get(_rule_key(item))
        if rule is None:
            continue
        if (
            key not in applicability
            or applicability[key].state is not ApplicabilityState.APPLICABLE
        ):
            raise PrototypeContractError("observation lacks matching applicable canonical task")
        if item.state is SatisfactionState.SATISFIED:
            expected_action = rule.required_action
        elif item.state is SatisfactionState.UNSATISFIED:
            expected_action = f"absence:{rule.required_action}"
        else:
            expected_action = None
        if expected_action is not None and (
            not item.authoritative or item.observed_action != expected_action
        ):
            raise PrototypeContractError(
                "observable outcome requires authoritative exact action contract"
            )
        if item.state is SatisfactionState.UNKNOWN and item.observed_action is not None:
            _token(item.observed_action, "observed action")
        previous = result.get(key)
        if previous is not None and previous is not item.state:
            raise PrototypeContractError("conflicting canonical task observations")
        result[key] = item.state
    return result


def _hash(value: object, label: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise PrototypeContractError(f"{label} must be an exact privacy-safe fingerprint")


def _token(value: object, label: str) -> None:
    if not isinstance(value, str) or not _SAFE_TOKEN.fullmatch(value):
        raise PrototypeContractError(f"{label} must be a privacy-safe token")


def _time(value: object, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PrototypeContractError(f"{label} must be timezone-aware")

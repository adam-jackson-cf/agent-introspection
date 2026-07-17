"""Versioned deterministic detectors over normalized telemetry events."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from .normalization import NORMALIZATION_VERSION, NormalizedOperation

DETECTOR_VERSIONS: Mapping[str, int] = {
    "tool_failure": 1,
    "repeated_attempt": 1,
    "transport_instability": 1,
    "sandbox_friction": 1,
    "turn_correction": 1,
    "quality_gate_bypass": 1,
    "command_churn": 1,
    "tool_loop": 1,
    "token_outlier": 1,
    "skill_adherence": 1,
    "scope_recurrence": 1,
}

_TRANSPORT_EVENT_NAMES = frozenset(
    {
        "codex.api_request",
        "codex.api_response",
        "codex.websocket_request",
        "codex.websocket_response",
    }
)


@dataclass(frozen=True, slots=True)
class DetectorEvent:
    event_id: str
    timestamp: datetime
    project_id: str
    task_id: str
    event_name: str
    operation: NormalizedOperation | None = None
    success_string: str | None = None
    success_bool: bool | None = None
    status_code: int | None = None
    outcome: str | None = None
    is_mutation: bool = False
    token_count: int | None = None
    skill_required: str | None = None
    skill_used: bool | None = None
    decision_source: str | None = None
    is_quality_gate: bool | None = None
    tool_call_count: int | None = None
    counts_as_distinct_task: bool = True


@dataclass(frozen=True, slots=True)
class FingerprintComponents:
    detector_version: int
    category: str
    project_identity: str
    operation_kind: str
    target_kind: str
    normalized_target: str
    normalized_failure_class: str

    def digest(self) -> str:
        payload = json.dumps(
            {
                "detector_version": self.detector_version,
                "category": self.category,
                "project_identity": self.project_identity,
                "operation_kind": self.operation_kind,
                "target_kind": self.target_kind,
                "normalized_target": self.normalized_target,
                "normalized_failure_class": self.normalized_failure_class,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Observation:
    detector_id: str
    detector_version: int
    category: str
    project_id: str
    task_ids: tuple[str, ...]
    event_ids: tuple[str, ...]
    fingerprint: str
    fingerprint_components: FingerprintComponents
    normalization_version: int
    membership_explanation: str


def _operation_fields(event: DetectorEvent) -> tuple[str, str, str]:
    operation = event.operation
    if operation is None:
        return "event", "none", event.event_name
    target = operation.target or operation.executable
    if operation.target is None:
        target_kind = "executable"
    elif operation.target.startswith(("http://", "https://", "file://")):
        target_kind = "uri"
    else:
        target_kind = "path"
    return operation.kind, target_kind, target


def _observation(
    detector_id: str,
    events: Sequence[DetectorEvent],
    *,
    failure_class: str,
    explanation: str,
) -> Observation:
    if not events:
        raise ValueError("observation requires membership evidence")
    ordered = sorted(events, key=lambda event: (event.timestamp, event.event_id))
    first = ordered[0]
    operation_kind, target_kind, normalized_target = _operation_fields(first)
    version = DETECTOR_VERSIONS[detector_id]
    components = FingerprintComponents(
        detector_version=version,
        category=detector_id,
        project_identity=first.project_id,
        operation_kind=operation_kind,
        target_kind=target_kind,
        normalized_target=normalized_target,
        normalized_failure_class=failure_class,
    )
    return Observation(
        detector_id=detector_id,
        detector_version=version,
        category=detector_id,
        project_id=first.project_id,
        task_ids=tuple(sorted({event.task_id for event in ordered})),
        event_ids=tuple(event.event_id for event in ordered),
        fingerprint=components.digest(),
        fingerprint_components=components,
        normalization_version=NORMALIZATION_VERSION,
        membership_explanation=explanation,
    )


def _tool_failure_class(event: DetectorEvent) -> str | None:
    if event.event_name == "codex.tool_result" and event.success_string == "false":
        return "tool_result_false"
    if event.event_name in _TRANSPORT_EVENT_NAMES:
        if event.success_bool is False:
            return "transport_success_false"
        if event.status_code is not None and event.status_code >= 400:
            return f"http_{event.status_code // 100}xx"
    return None


def _quality_key(event: DetectorEvent) -> tuple[object, ...] | None:
    operation = event.operation
    if operation is None or operation.kind != "shell":
        return None
    if event.is_quality_gate is False:
        return None
    established_quality_tools = {
        "pytest",
        "ruff",
        "mypy",
        "npm",
        "pnpm",
        "yarn",
        "bun",
        "cargo",
        "go",
        "make",
    }
    if event.is_quality_gate is not True and operation.executable not in established_quality_tools:
        return None
    return operation.membership_key


def _nearest_rank_p95(values: Sequence[int]) -> int:
    if not values:
        raise ValueError("p95 requires at least one baseline value")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
        raise ValueError("baseline values must be non-negative integers")
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def _baseline_for(
    metric: str,
    event: DetectorEvent,
    *,
    project_or_metric_baselines: Mapping[str, Sequence[int]],
    task_baselines: Mapping[str, Sequence[int]],
) -> Sequence[int] | None:
    if metric == "token":
        task_keys: tuple[str, ...] = (f"{event.task_id}:token", event.task_id)
        project_keys: tuple[str, ...] = (f"{event.project_id}:token", event.project_id)
    else:
        task_keys = (f"{event.task_id}:tool_calls", event.task_id)
        project_keys = (f"{event.project_id}:tool_calls",)
    for key in (*task_keys, *project_keys):
        if key in task_baselines:
            return task_baselines[key]
    for key in (*task_keys, *project_keys):
        if key in project_or_metric_baselines:
            return project_or_metric_baselines[key]
    return None


def _loop_indexes(task_events: Sequence[DetectorEvent]) -> tuple[int, ...]:
    operation_indexes = [
        index for index, event in enumerate(task_events) if event.operation is not None
    ]
    keys: list[tuple[object, ...]] = []
    for index in operation_indexes:
        operation = task_events[index].operation
        if operation is None:
            raise ValueError("operation index lost its operation")
        keys.append(operation.membership_key)
    candidates: list[tuple[int, int, tuple[int, ...]]] = []
    for start in range(len(keys)):
        remaining = len(keys) - start
        for cycle_length in range(1, remaining // 2 + 1):
            cycle = keys[start : start + cycle_length]
            if any(key is None for key in cycle):
                continue
            repetitions = 2
            while (
                start + (repetitions + 1) * cycle_length <= len(keys)
                and keys[
                    start + repetitions * cycle_length : start + (repetitions + 1) * cycle_length
                ]
                == cycle
            ):
                repetitions += 1
            total = repetitions * cycle_length
            if cycle_length == 1 and total < 3:
                continue
            if cycle_length > 1 and total < 4:
                continue
            candidates.append(
                (
                    total,
                    start,
                    tuple(operation_indexes[start : start + total]),
                )
            )
    if not candidates:
        return ()
    _, _, indexes = max(candidates, key=lambda candidate: (candidate[0], -candidate[1]))
    return indexes


class DetectorEngine:
    """Run all deterministic detectors with stable ordering and membership evidence."""

    def detect(
        self,
        events: Sequence[DetectorEvent],
        *,
        token_baselines: Mapping[str, Sequence[int]] | None = None,
        task_baselines: Mapping[str, Sequence[int]] | None = None,
    ) -> tuple[Observation, ...]:
        ordered = sorted(events, key=lambda event: (event.timestamp, event.event_id))
        observations: list[Observation] = []
        project_or_metric_baselines = token_baselines or {}
        scoped_task_baselines = task_baselines or {}

        for event in ordered:
            failure = _tool_failure_class(event)
            if failure is not None:
                observations.append(
                    _observation(
                        "tool_failure",
                        (event,),
                        failure_class=failure,
                        explanation=(
                            f"{event.event_name} satisfies the explicit {failure} predicate"
                        ),
                    )
                )
            if event.event_name in _TRANSPORT_EVENT_NAMES and (
                event.success_bool is False
                or (event.status_code is not None and event.status_code >= 400)
            ):
                observations.append(
                    _observation(
                        "transport_instability",
                        (event,),
                        failure_class=failure or "transport_failure",
                        explanation=(
                            "API or WebSocket telemetry records an explicit transport failure"
                        ),
                    )
                )
            if event.event_name == "codex.sandbox_outcome":
                observations.append(
                    _observation(
                        "sandbox_friction",
                        (event,),
                        failure_class=event.outcome or "sandbox_outcome",
                        explanation="event is an observed codex.sandbox_outcome",
                    )
                )
            if event.event_name in {"turn/interrupt", "turn/steer"}:
                observations.append(
                    _observation(
                        "turn_correction",
                        (event,),
                        failure_class=event.event_name,
                        explanation=f"trace contains {event.event_name}",
                    )
                )
            if event.event_name == "codex.tool_decision" and event.decision_source == "user":
                observations.append(
                    _observation(
                        "turn_correction",
                        (event,),
                        failure_class="user_tool_decision",
                        explanation="codex.tool_decision records an explicit user-sourced decision",
                    )
                )
            if event.skill_required is not None and event.skill_used is False:
                observations.append(
                    _observation(
                        "skill_adherence",
                        (event,),
                        failure_class="required_skill_not_used",
                        explanation=(
                            f"required skill {event.skill_required!r} was explicitly not used"
                        ),
                    )
                )

        by_task: dict[tuple[str, str], list[DetectorEvent]] = defaultdict(list)
        for event in ordered:
            by_task[(event.project_id, event.task_id)].append(event)

        for task_events in by_task.values():
            operation_groups: dict[tuple[object, ...], list[DetectorEvent]] = defaultdict(list)
            for event in task_events:
                if event.operation is not None:
                    operation_groups[event.operation.membership_key].append(event)
            for members in operation_groups.values():
                if len(members) >= 2:
                    observations.append(
                        _observation(
                            "repeated_attempt",
                            members,
                            failure_class="same_normalized_operation",
                            explanation=(
                                "the same normalized operation occurred "
                                f"{len(members)} times in one task"
                            ),
                        )
                    )

            shell_by_target: dict[str, list[DetectorEvent]] = defaultdict(list)
            for event in task_events:
                operation = event.operation
                if operation is not None and operation.kind == "shell" and operation.target:
                    shell_by_target[operation.target].append(event)
            for members in shell_by_target.values():
                distinct = {event.operation.membership_key for event in members if event.operation}
                if len(distinct) >= 3:
                    observations.append(
                        _observation(
                            "command_churn",
                            members,
                            failure_class="three_distinct_commands_same_target",
                            explanation=(
                                f"{len(distinct)} distinct normalized shell commands targeted "
                                "the same path"
                            ),
                        )
                    )

            loop_indexes = set(_loop_indexes(task_events))
            if loop_indexes:
                members = [task_events[index] for index in sorted(loop_indexes)]
                observations.append(
                    _observation(
                        "tool_loop",
                        members,
                        failure_class="repeating_contiguous_operation_cycle",
                        explanation="contiguous normalized operations form a repeated cycle",
                    )
                )

            failed: dict[tuple[object, ...], DetectorEvent] = {}
            mutations: dict[tuple[object, ...], list[DetectorEvent]] = defaultdict(list)
            for event in task_events:
                quality_key = _quality_key(event)
                if (
                    quality_key is not None
                    and event.operation is not None
                    and event.operation.exit_code is not None
                ):
                    if event.operation.exit_code != 0:
                        failed[quality_key] = event
                        mutations[quality_key] = []
                    elif quality_key in failed:
                        if mutations[quality_key]:
                            members = [failed[quality_key], *mutations[quality_key], event]
                            observations.append(
                                _observation(
                                    "quality_gate_bypass",
                                    members,
                                    failure_class="mutation_between_failed_and_passing_gate",
                                    explanation=(
                                        "a failed quality command was followed by mutation before "
                                        "same normalized command passed"
                                    ),
                                )
                            )
                        failed.pop(quality_key)
                        mutations.pop(quality_key)
                    continue
                if event.is_mutation:
                    for key in failed:
                        mutations[key].append(event)

        scope_groups: dict[tuple[str, str], list[DetectorEvent]] = defaultdict(list)
        for event in ordered:
            if event.operation is not None and event.operation.target:
                scope_groups[(event.project_id, event.operation.target)].append(event)
        for members in scope_groups.values():
            tasks = {event.task_id for event in members if event.counts_as_distinct_task}
            if len(tasks) >= 2:
                observations.append(
                    _observation(
                        "scope_recurrence",
                        members,
                        failure_class="same_scope_multiple_canonical_tasks",
                        explanation=(
                            f"the normalized target recurred across {len(tasks)} canonical tasks"
                        ),
                    )
                )

        for event in ordered:
            metrics = (("token", event.token_count), ("tool_call", event.tool_call_count))
            for metric, value in metrics:
                if value is None:
                    continue
                baseline = _baseline_for(
                    metric,
                    event,
                    project_or_metric_baselines=project_or_metric_baselines,
                    task_baselines=scoped_task_baselines,
                )
                if baseline is None or len(baseline) < 20:
                    continue
                threshold = _nearest_rank_p95(baseline)
                if value <= threshold:
                    continue
                observations.append(
                    _observation(
                        "token_outlier",
                        (event,),
                        failure_class=f"{metric}_above_project_p95",
                        explanation=(
                            f"{metric} count {value} exceeds p95 {threshold} from "
                            f"{len(baseline)} comparable episodes"
                        ),
                    )
                )

        unique: dict[tuple[str, str, tuple[str, ...]], Observation] = {}
        for observation in observations:
            key = (observation.detector_id, observation.fingerprint, observation.event_ids)
            unique[key] = observation
        return tuple(
            sorted(
                unique.values(),
                key=lambda item: (item.detector_id, item.project_id, item.event_ids),
            )
        )

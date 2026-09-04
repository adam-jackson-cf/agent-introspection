"""Pure, fail-closed E-Task-2 reducer for privacy-safe task operation evidence."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from experiments.dashboard_prototype.contracts import SUPPORTED_PRODUCERS, PrototypeContractError
from experiments.dashboard_prototype.task_common import SUPPORTED_SURFACES

_SAFE_TOKEN = re.compile(r"[a-z][a-z0-9._:-]{0,127}\Z", re.ASCII)


class TaskOperationOutcome(StrEnum):
    FAILED = "failed"
    PASSED = "passed"


class TaskTerminalOutcome(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class TaskOperation:
    producer: str
    surface: str
    native_session_hash: str
    task_id: str
    project_state: str
    call_id: str
    source_time: datetime
    source_order: int
    operation: str
    target: str
    failure: str | None
    outcome: TaskOperationOutcome | None
    command_fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class TaskTerminal:
    producer: str
    surface: str
    native_session_hash: str
    task_id: str
    project_state: str
    outcome: TaskTerminalOutcome
    source_time: datetime
    native_identity_proven: bool
    terminal_outcome_proven: bool
    evidence_id: str


@dataclass(frozen=True, slots=True)
class TaskReductionBoundary:
    source_start: datetime
    source_end: datetime
    operations: Iterable[TaskOperation]
    terminals: Iterable[TaskTerminal] = ()


@dataclass(frozen=True, slots=True)
class FailedCallGroup:
    operation: str
    target: str
    failure: str
    call_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecoveredFailure:
    failed_call_ids: tuple[str, ...]
    recovery_call_id: str
    operation: str
    target: str


@dataclass(frozen=True, slots=True)
class CommandChurn:
    target: str
    command_fingerprints: tuple[str, ...]
    call_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OperationLoop:
    operations: tuple[str, ...]
    call_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QualitySequence:
    failed_call_id: str
    mutation_call_ids: tuple[str, ...]
    passed_call_id: str
    target: str
    command_fingerprint: str


@dataclass(frozen=True, slots=True)
class TaskReduction:
    producer: str
    surface: str
    native_session_hash: str
    task_id: str
    project_state: str
    call_ids: tuple[str, ...]
    received_delivery_count: int
    duplicate_delivery_count: int
    operation_count: int
    known_outcome_count: int
    unknown_outcome_count: int
    terminal_outcome: TaskTerminalOutcome | None
    failed_call_groups: tuple[FailedCallGroup, ...]
    recovered_failures: tuple[RecoveredFailure, ...]
    repeated_attempt_groups: tuple[tuple[str, ...], ...]
    command_churn: tuple[CommandChurn, ...]
    contiguous_loops: tuple[OperationLoop, ...]
    sandbox_friction_call_ids: tuple[str, ...]
    quality_sequences: tuple[QualitySequence, ...]
    recovery_percentage: float | None
    recovery_denominator: int | None


def reduce_task_operations(*, boundary: TaskReductionBoundary) -> tuple[TaskReduction, ...]:
    _validate_window(boundary)
    calls, received = _deduplicate(boundary)
    terminal_by_task = _index_terminals(boundary, calls)
    grouped: dict[tuple[str, str, str, str, str], list[TaskOperation]] = {}
    canonical_task_lineages: dict[str, tuple[str, str, str, str]] = {}
    for call in calls:
        lineage = (
            call.producer,
            call.surface,
            call.native_session_hash,
            call.project_state,
        )
        known = canonical_task_lineages.setdefault(call.task_id, lineage)
        if known != lineage:
            raise PrototypeContractError("task identity has conflicting lineage")
        grouped.setdefault(_lineage(call), []).append(call)
    return tuple(
        _reduce_task(items, received[_lineage(items[0])], terminal_by_task.get(_lineage(items[0])))
        for _, items in sorted(grouped.items())
    )


def _validate_window(boundary: TaskReductionBoundary) -> None:
    _require_time(boundary.source_start, "source start")
    _require_time(boundary.source_end, "source end")
    if boundary.source_start >= boundary.source_end:
        raise PrototypeContractError("source boundary must be positive")


def _lineage(item: TaskOperation | TaskTerminal) -> tuple[str, str, str, str, str]:
    return (item.producer, item.surface, item.native_session_hash, item.task_id, item.project_state)


def _deduplicate(
    boundary: TaskReductionBoundary,
) -> tuple[tuple[TaskOperation, ...], dict[tuple[str, str, str, str, str], int]]:
    calls: dict[tuple[str, str, str], TaskOperation] = {}
    received: dict[tuple[str, str, str, str, str], int] = {}
    for item in boundary.operations:
        _validate_operation(item, boundary)
        received[_lineage(item)] = received.get(_lineage(item), 0) + 1
        identity = (item.producer, item.native_session_hash, item.call_id)
        prior = calls.get(identity)
        if prior is not None and prior != item:
            raise PrototypeContractError(
                "call identity was delivered with conflicting immutable content"
            )
        calls.setdefault(identity, item)
    ordered = tuple(sorted(calls.values(), key=lambda item: (item.source_time, item.source_order)))
    for lineage in {_lineage(item) for item in ordered}:
        _validate_order([item for item in ordered if _lineage(item) == lineage])
    return ordered, received


def _index_terminals(
    boundary: TaskReductionBoundary, calls: tuple[TaskOperation, ...]
) -> dict[tuple[str, str, str, str, str], TaskTerminal]:
    terminals: dict[tuple[str, str, str, str, str], TaskTerminal] = {}
    call_lineages = {_lineage(call) for call in calls}
    for terminal in boundary.terminals:
        _validate_terminal(terminal, boundary)
        lineage = _lineage(terminal)
        if lineage not in call_lineages:
            raise PrototypeContractError("terminal has no matching canonical task")
        if terminal.source_time <= max(
            call.source_time for call in calls if _lineage(call) == lineage
        ):
            raise PrototypeContractError("terminal must follow every task call")
        if lineage in terminals:
            raise PrototypeContractError("task has conflicting terminal authority")
        terminals[lineage] = terminal
    return terminals


def _validate_operation(item: TaskOperation, boundary: TaskReductionBoundary) -> None:
    for value, label in (
        (item.producer, "producer"),
        (item.surface, "surface"),
        (item.native_session_hash, "native session hash"),
        (item.task_id, "task identity"),
        (item.project_state, "project state"),
        (item.call_id, "call identity"),
        (item.operation, "operation"),
    ):
        _require_token(value, label)
    _require_operation(item.operation)
    _require_fingerprint(item.target, "target")
    if (
        item.producer not in SUPPORTED_PRODUCERS
        or (item.producer, item.surface) not in SUPPORTED_SURFACES
    ):
        raise PrototypeContractError("operation producer/surface is unsupported")
    _require_time(item.source_time, "source time")
    if not boundary.source_start < item.source_time <= boundary.source_end:
        raise PrototypeContractError("operation falls outside source membership boundary")
    if (
        not isinstance(item.source_order, int)
        or isinstance(item.source_order, bool)
        or item.source_order < 0
    ):
        raise PrototypeContractError("source order must be non-negative integer")
    if item.outcome is not None and not isinstance(item.outcome, TaskOperationOutcome):
        raise PrototypeContractError("operation outcome must be explicit or absent")
    if item.outcome is TaskOperationOutcome.FAILED:
        _require_failure(item.failure)
    elif item.failure is not None:
        raise PrototypeContractError("only failed operations may retain a failure")
    if item.command_fingerprint is not None:
        _require_fingerprint(item.command_fingerprint, "command")


def _validate_terminal(item: TaskTerminal, boundary: TaskReductionBoundary) -> None:
    for value, label in (
        (item.producer, "terminal producer"),
        (item.surface, "terminal surface"),
        (item.native_session_hash, "terminal session"),
        (item.task_id, "terminal task"),
        (item.project_state, "terminal project"),
    ):
        _require_token(value, label)
    if (item.producer, item.surface) not in SUPPORTED_SURFACES or not isinstance(
        item.outcome, TaskTerminalOutcome
    ):
        raise PrototypeContractError("terminal authority is unsupported")
    if not item.native_identity_proven or not item.terminal_outcome_proven:
        raise PrototypeContractError("terminal lacks authoritative native outcome evidence")
    _require_token(item.evidence_id, "terminal evidence identity")
    _require_time(item.source_time, "terminal source time")
    if not boundary.source_start < item.source_time <= boundary.source_end:
        raise PrototypeContractError("terminal falls outside source membership boundary")


def _validate_order(items: list[TaskOperation]) -> None:
    orders = [item.source_order for item in items]
    if len(set(orders)) != len(orders):
        raise PrototypeContractError("task source ordering contains a tie")
    if orders != list(range(len(orders))):
        raise PrototypeContractError("task source ordering is incomplete")


def _reduce_task(
    items: list[TaskOperation], received: int, terminal: TaskTerminal | None
) -> TaskReduction:
    items.sort(key=lambda item: (item.source_time, item.source_order))
    first = items[0]
    groups = _failed_groups(items)
    recovered = _recoveries(items, groups)
    return TaskReduction(
        first.producer,
        first.surface,
        first.native_session_hash,
        first.task_id,
        first.project_state,
        tuple(item.call_id for item in items),
        received,
        received - len(items),
        len(items),
        sum(item.outcome is not None for item in items),
        sum(item.outcome is None for item in items),
        None if terminal is None else terminal.outcome,
        groups,
        recovered,
        _repeated_attempts(items),
        _command_churn(items),
        _loops(items),
        tuple(
            item.call_id
            for item in items
            if item.outcome is TaskOperationOutcome.FAILED
            and item.failure in {"sandbox", "permission"}
        ),
        _quality_sequences(items),
        None if terminal is None else (100.0 * len(recovered) / len(groups) if groups else None),
        None if terminal is None else len(groups),
    )


def _failed_groups(items: list[TaskOperation]) -> tuple[FailedCallGroup, ...]:
    groups: list[FailedCallGroup] = []
    for item in items:
        if item.outcome is TaskOperationOutcome.FAILED:
            assert item.failure is not None
            if groups and (groups[-1].operation, groups[-1].target, groups[-1].failure) == (
                item.operation,
                item.target,
                item.failure,
            ):
                groups[-1] = FailedCallGroup(
                    item.operation, item.target, item.failure, (*groups[-1].call_ids, item.call_id)
                )
            else:
                groups.append(
                    FailedCallGroup(item.operation, item.target, item.failure, (item.call_id,))
                )
    return tuple(groups)


def _recoveries(
    items: list[TaskOperation], groups: tuple[FailedCallGroup, ...]
) -> tuple[RecoveredFailure, ...]:
    index = {item.call_id: index for index, item in enumerate(items)}
    result = []
    for group in groups:
        match = next(
            (
                item
                for item in items[index[group.call_ids[-1]] + 1 :]
                if item.operation == group.operation
                and item.target == group.target
                and item.outcome is TaskOperationOutcome.PASSED
            ),
            None,
        )
        if match:
            result.append(
                RecoveredFailure(group.call_ids, match.call_id, group.operation, group.target)
            )
    return tuple(result)


def _repeated_attempts(items: list[TaskOperation]) -> tuple[tuple[str, ...], ...]:
    groups: dict[tuple[str, str], list[str]] = {}
    for item in items:
        groups.setdefault((item.operation, item.target), []).append(item.call_id)
    return tuple(tuple(ids) for _, ids in sorted(groups.items()) if len(ids) >= 2)


def _command_churn(items: list[TaskOperation]) -> tuple[CommandChurn, ...]:
    by_target: dict[str, list[TaskOperation]] = {}
    for item in items:
        if item.command_fingerprint is not None:
            by_target.setdefault(item.target, []).append(item)
    return tuple(
        CommandChurn(
            target,
            tuple(sorted({item.command_fingerprint for item in calls if item.command_fingerprint})),
            tuple(item.call_id for item in calls),
        )
        for target, calls in sorted(by_target.items())
        if len({item.command_fingerprint for item in calls}) >= 3
    )


def _loops(items: list[TaskOperation]) -> tuple[OperationLoop, ...]:
    operations = [item.operation for item in items]
    candidates: list[tuple[int, int, int]] = []
    for start in range(len(items)):
        for width in range(1, (len(items) - start) // 2 + 1):
            end = start + width * 2
            pattern = operations[start : start + width]
            if operations[start + width : end] != pattern:
                continue
            while end + width <= len(items) and operations[end : end + width] == pattern:
                end += width
            if width == 1 and end - start < 3:
                continue
            candidates.append((start, end, width))
    maximal = [
        candidate
        for candidate in candidates
        if not any(
            other[2] == candidate[2]
            and other[0] <= candidate[0]
            and candidate[1] <= other[1]
            and other != candidate
            for other in candidates
        )
    ]
    selected: list[tuple[int, int, int]] = []
    for candidate in sorted(maximal, key=lambda value: (value[0], -value[1], value[2])):
        if any(candidate[0] < end and start < candidate[1] for start, end, _ in selected):
            continue
        selected.append(candidate)
    return tuple(
        OperationLoop(
            tuple(operations[start:end]),
            tuple(item.call_id for item in items[start:end]),
        )
        for start, end, _ in selected
    )


def _quality_sequences(items: list[TaskOperation]) -> tuple[QualitySequence, ...]:
    result = []
    for index, failed in enumerate(items):
        if failed.outcome is not TaskOperationOutcome.FAILED or failed.command_fingerprint is None:
            continue
        tail = items[index + 1 :]
        passed = next(
            (
                item
                for item in tail
                if item.operation == failed.operation
                and item.target == failed.target
                and item.command_fingerprint == failed.command_fingerprint
                and item.outcome is TaskOperationOutcome.PASSED
            ),
            None,
        )
        if passed is None:
            continue
        pass_index = items.index(passed, index + 1)
        mutations = tuple(
            item.call_id
            for item in items[index + 1 : pass_index]
            if item.operation in {"mutate", "edit", "patch", "write"}
        )
        if mutations:
            result.append(
                QualitySequence(
                    failed.call_id,
                    mutations,
                    passed.call_id,
                    failed.target,
                    failed.command_fingerprint,
                )
            )
    return tuple(result)


def _require_token(value: object, label: str) -> None:
    if not isinstance(value, str) or not _SAFE_TOKEN.fullmatch(value):
        raise PrototypeContractError(f"{label} must be a privacy-safe token")


def _require_fingerprint(value: object, kind: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(rf"{kind}:[0-9a-f]{{64}}", value):
        raise PrototypeContractError(f"{kind} must be an exact privacy-safe fingerprint")


def _require_failure(value: object) -> None:
    if value not in {"sandbox", "permission"}:
        _require_fingerprint(value, "failure")


def _require_operation(value: str) -> None:
    if value.startswith(("sk-", "token", "secret", "credential", "api-key", "api_key")):
        raise PrototypeContractError("operation taxonomy rejects credential-like identifiers")


def _require_time(value: object, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise PrototypeContractError(f"{label} must be timezone-aware")

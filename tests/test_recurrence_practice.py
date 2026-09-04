import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from experiments.dashboard_prototype.contracts import PrototypeContractError
from experiments.dashboard_prototype.recurrence_practice import (
    M14_FIRST_OCCURRENCES,
    PracticeBoundary,
    PracticeOperation,
    PracticeTerminal,
    PracticeTerminalOutcome,
    reduce_successful_practice,
)

START = datetime(2026, 9, 1, 23, tzinfo=UTC)
END = START + timedelta(days=7)


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def operation(
    attempt_id: str,
    task_id: str,
    order: int,
    when: datetime,
    owner: str = "unowned",
) -> PracticeOperation:
    return PracticeOperation(
        "omp",
        "omp",
        attempt_id,
        task_id,
        when,
        order,
        fingerprint("op-a"),
        fingerprint("target-a"),
        "task-class-v1",
        fingerprint("project-a"),
        "model-v1",
        owner,
        1,
        True,
        True,
        True,
    )


def terminal(
    attempt_id: str, when: datetime, outcome: PracticeTerminalOutcome | None
) -> PracticeTerminal:
    return PracticeTerminal("omp", "omp", attempt_id, when, outcome, True)


def test_sequence_order_requires_contiguous_complete_ordinals() -> None:
    rows = (operation("attempt-a", "task-a", 1, START + timedelta(minutes=1)),)
    with pytest.raises(PrototypeContractError, match="contiguous"):
        reduce_successful_practice(boundary=PracticeBoundary(START, END, rows, ()))


def test_explicit_success_and_task_distinct_m14_first_alternative() -> None:
    operations = tuple(
        operation(f"attempt-{index}", f"task-{index}", 0, START + timedelta(days=index + 1))
        for index in range(M14_FIRST_OCCURRENCES)
    )
    terminals = tuple(
        terminal(
            f"attempt-{index}",
            START + timedelta(days=index + 1, minutes=1),
            PracticeTerminalOutcome.SUCCESS,
        )
        for index in range(M14_FIRST_OCCURRENCES)
    )
    reduction = reduce_successful_practice(
        boundary=PracticeBoundary(START, END, operations, terminals)
    )
    sequence = reduction.sequences[0]
    assert sequence.m14_threshold_met is True
    assert sequence.successful_task_count == M14_FIRST_OCCURRENCES
    assert sequence.support_percentage == 100.0
    assert sequence.success_percentage == 100.0
    assert reduction.candidate_count == M14_FIRST_OCCURRENCES


def test_sequence_success_percentage_uses_terminal_observable_sequence_tasks() -> None:
    operations = tuple(
        operation(f"attempt-{index}", f"task-{index}", 0, START + timedelta(days=index + 1))
        for index in range(4)
    )
    terminals = tuple(
        terminal(
            f"attempt-{index}",
            START + timedelta(days=index + 1, minutes=1),
            PracticeTerminalOutcome.SUCCESS if index < 3 else PracticeTerminalOutcome.FAILURE,
        )
        for index in range(4)
    )
    reduction = reduce_successful_practice(
        boundary=PracticeBoundary(START, END, operations, terminals)
    )
    assert reduction.sequences[0].success_percentage == 75.0


def test_unknown_terminal_withholds_comparable_denominators() -> None:
    reduction = reduce_successful_practice(
        boundary=PracticeBoundary(
            START,
            END,
            (operation("attempt-a", "task-a", 0, START + timedelta(minutes=1)),),
            (terminal("attempt-a", START + timedelta(minutes=2), None),),
        )
    )
    assert reduction.comparable_task_count == 0
    assert reduction.successful_task_count == 0
    assert reduction.unknown_terminal_count == 1


def test_registry_owned_sequence_is_never_a_candidate() -> None:
    operations = tuple(
        operation(
            f"attempt-{index}",
            f"task-{index}",
            0,
            START + timedelta(days=index + 1),
            "owned",
        )
        for index in range(3)
    )
    terminals = tuple(
        terminal(
            f"attempt-{index}",
            START + timedelta(days=index + 1, minutes=1),
            PracticeTerminalOutcome.SUCCESS,
        )
        for index in range(3)
    )
    reduction = reduce_successful_practice(
        boundary=PracticeBoundary(START, END, operations, terminals)
    )
    assert reduction.sequences[0].m14_threshold_met is True
    assert reduction.sequences[0].registry_owned is True
    assert reduction.candidate_count == 0


def test_producer_identity_remains_separate_from_task_identity() -> None:
    first = operation("attempt-a", "task-a", 0, START + timedelta(minutes=1))
    second = PracticeOperation(
        "codex-cli",
        "codex-cli",
        "attempt-a",
        "task-a",
        START + timedelta(minutes=1),
        0,
        fingerprint("op-a"),
        fingerprint("target-a"),
        "task-class-v1",
        fingerprint("project-a"),
        "model-v1",
        "unowned",
        1,
        True,
        True,
        True,
    )
    terminals = (
        terminal("attempt-a", START + timedelta(minutes=2), PracticeTerminalOutcome.SUCCESS),
        PracticeTerminal(
            "codex-cli",
            "codex-cli",
            "attempt-a",
            START + timedelta(minutes=2),
            PracticeTerminalOutcome.SUCCESS,
            True,
        ),
    )
    reduction = reduce_successful_practice(
        boundary=PracticeBoundary(START, END, (first, second), terminals)
    )
    assert reduction.successful_task_count == 2

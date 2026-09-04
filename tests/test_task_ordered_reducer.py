from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from experiments.dashboard_prototype.contracts import PrototypeContractError
from experiments.dashboard_prototype.task_ordered_reducer import (
    TaskOperation,
    TaskOperationOutcome,
    TaskReductionBoundary,
    TaskTerminal,
    TaskTerminalOutcome,
    reduce_task_operations,
)

BASE = datetime(2026, 9, 2, tzinfo=UTC)


def operation(number: int, **changes: object) -> TaskOperation:
    values: dict[str, object] = {
        "producer": "omp",
        "surface": "omp",
        "native_session_hash": "session-hash",
        "task_id": "task-hash",
        "project_state": "project-hash",
        "call_id": f"call-{number}",
        "source_time": BASE + timedelta(seconds=number + 1),
        "source_order": number,
        "operation": "test",
        "target": "target:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "failure": None,
        "outcome": TaskOperationOutcome.PASSED,
        "command_fingerprint": None,
    }
    values.update(changes)
    return TaskOperation(**values)


def reduce(*operations: TaskOperation):
    first = operations[0]
    terminal = TaskTerminal(
        first.producer,
        first.surface,
        first.native_session_hash,
        first.task_id,
        first.project_state,
        TaskTerminalOutcome.COMPLETED,
        BASE + timedelta(seconds=59),
        True,
        True,
        "evidence-id",
    )
    return reduce_task_operations(
        boundary=TaskReductionBoundary(
            BASE,
            BASE + timedelta(minutes=1),
            operations,
            (terminal,),
        )
    )


def test_materializes_ordered_failures_recovery_churn_loops_and_quality_sequence() -> None:
    calls = (
        operation(
            3,
            operation="shell",
            target="target:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            command_fingerprint="command:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        ),
        operation(
            0,
            outcome=TaskOperationOutcome.FAILED,
            failure="sandbox",
            command_fingerprint="command:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
        ),
        operation(1, operation="edit", outcome=TaskOperationOutcome.PASSED),
        operation(
            2,
            command_fingerprint="command:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
        ),
        operation(
            4,
            operation="shell",
            target="target:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            command_fingerprint="command:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        ),
        operation(
            5,
            operation="shell",
            target="target:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            command_fingerprint="command:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
        ),
        operation(6, operation="poll"),
        operation(7, operation="poll"),
        operation(8, operation="poll"),
        operation(9, operation="a"),
        operation(10, operation="b"),
        operation(11, operation="a"),
        operation(12, operation="b"),
    )
    result = reduce(*reversed(calls))[0]
    assert result.call_ids == tuple(f"call-{number}" for number in range(13))
    assert result.operation_count == result.known_outcome_count == 13
    assert result.unknown_outcome_count == 0
    assert result.failed_call_groups[0].call_ids == ("call-0",)
    assert result.recovered_failures[0].recovery_call_id == "call-2"
    assert result.recovery_denominator == 1
    assert result.recovery_percentage == 100.0
    assert result.quality_sequences[0].mutation_call_ids == ("call-1",)
    assert result.sandbox_friction_call_ids == ("call-0",)
    assert result.command_churn[0].command_fingerprints == (
        "command:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        "command:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        "command:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
    )
    assert {loop.operations for loop in result.contiguous_loops} >= {
        ("poll", "poll", "poll"),
        ("a", "b", "a", "b"),
    }


def test_uses_source_order_for_equal_timestamps_and_accepts_the_end_boundary() -> None:
    end = BASE + timedelta(minutes=1)
    result = reduce_task_operations(
        boundary=TaskReductionBoundary(
            BASE,
            end,
            (
                operation(1, source_time=BASE + timedelta(seconds=10)),
                operation(0, source_time=BASE + timedelta(seconds=10)),
            ),
        )
    )[0]
    assert result.call_ids == ("call-0", "call-1")
    assert reduce_task_operations(
        boundary=TaskReductionBoundary(BASE, end, (operation(0, source_time=end),))
    )[0].call_ids == ("call-0",)


def test_empty_population_is_conserved() -> None:
    assert (
        reduce_task_operations(
            boundary=TaskReductionBoundary(BASE, BASE + timedelta(minutes=1), ())
        )
        == ()
    )


def test_duplicate_delivery_is_deduplicated_but_conflicting_call_is_rejected() -> None:
    exact = operation(0)
    assert reduce(exact, exact)[0].operation_count == 1
    with pytest.raises(PrototypeContractError, match="conflicting"):
        reduce(
            exact,
            replace(
                exact,
                target="target:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            ),
        )


def test_unknown_outcome_withholds_outcome_dependent_recovery_aggregates() -> None:
    result = reduce(
        operation(0, outcome=TaskOperationOutcome.FAILED, failure="sandbox"),
        operation(1, outcome=None),
    )[0]
    assert result.failed_call_groups[0].failure == "sandbox"
    assert result.recovered_failures == ()
    assert result.recovery_percentage == 0.0
    assert result.recovery_denominator == 1
    assert result.known_outcome_count == 1
    assert result.unknown_outcome_count == 1


def test_repeated_attempts_and_unrecovered_failure_preserve_population() -> None:
    result = reduce(
        operation(0, outcome=TaskOperationOutcome.FAILED, failure="sandbox"),
        operation(1, outcome=TaskOperationOutcome.FAILED, failure="sandbox"),
        operation(2, operation="other"),
    )[0]
    assert result.failed_call_groups[0].call_ids == ("call-0", "call-1")
    assert result.repeated_attempt_groups == (("call-0", "call-1"),)
    assert result.recovery_denominator == 1
    assert result.recovery_percentage == 0.0
    assert result.operation_count == len(result.call_ids)


@pytest.mark.parametrize(
    "invalid",
    [
        lambda: TaskReductionBoundary(BASE, BASE, (operation(0),)),
        lambda: TaskReductionBoundary(
            BASE, BASE + timedelta(minutes=1), (operation(0, source_time=BASE),)
        ),
        lambda: TaskReductionBoundary(
            BASE, BASE + timedelta(minutes=1), (operation(0, source_order=-1),)
        ),
        lambda: TaskReductionBoundary(
            BASE, BASE + timedelta(minutes=1), (operation(0, producer="unknown"),)
        ),
        lambda: TaskReductionBoundary(
            BASE, BASE + timedelta(minutes=1), (operation(0, surface="wrong"),)
        ),
        lambda: TaskReductionBoundary(
            BASE,
            BASE + timedelta(minutes=1),
            (operation(0, outcome=TaskOperationOutcome.PASSED, failure="exit"),),
        ),
        lambda: TaskReductionBoundary(
            BASE,
            BASE + timedelta(minutes=1),
            (operation(0, outcome=TaskOperationOutcome.FAILED, failure=None),),
        ),
    ],
)
def test_rejects_invalid_boundaries_and_operation_authority(invalid) -> None:
    with pytest.raises(PrototypeContractError):
        reduce_task_operations(boundary=invalid())


def test_rejects_tied_and_incomplete_ordering_and_conflicting_lineage() -> None:
    with pytest.raises(PrototypeContractError, match="tie"):
        reduce(operation(0), operation(1, source_order=0))
    with pytest.raises(PrototypeContractError, match="incomplete"):
        reduce(operation(0, source_order=1))
    with pytest.raises(PrototypeContractError, match="lineage"):
        reduce(operation(0), operation(1, project_state="other-project", source_order=0))


def test_rejects_terminal_with_equal_call_timestamp() -> None:
    call = operation(0)
    terminal = TaskTerminal(
        call.producer,
        call.surface,
        call.native_session_hash,
        call.task_id,
        call.project_state,
        TaskTerminalOutcome.COMPLETED,
        call.source_time,
        True,
        True,
        "evidence-id",
    )
    with pytest.raises(PrototypeContractError, match="must follow"):
        reduce_task_operations(
            boundary=TaskReductionBoundary(
                BASE,
                BASE + timedelta(minutes=1),
                (call,),
                (terminal,),
            )
        )


def test_equal_native_session_hashes_across_producers_remain_separate() -> None:
    results = reduce(
        operation(0, task_id="omp-task"),
        operation(0, producer="codex-cli", surface="codex-cli", task_id="cli-task"),
    )
    assert [(item.producer, item.native_session_hash) for item in results] == [
        ("codex-cli", "session-hash"),
        ("omp", "session-hash"),
    ]

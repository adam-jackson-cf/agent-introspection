from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent_introspection.detectors import DetectorEngine, DetectorEvent
from agent_introspection.normalization import NormalizedOperation

BASE = datetime(2026, 7, 1, tzinfo=UTC)


def _operation(
    executable: str,
    subcommand: str | None,
    target: str | None,
    *,
    exit_code: int | None = None,
) -> NormalizedOperation:
    argv = tuple(item for item in (executable, subcommand, target) if item is not None)
    return NormalizedOperation("shell", executable, subcommand, target, argv, exit_code)


def _event(index: int, **changes: object) -> DetectorEvent:
    values: dict[str, object] = {
        "event_id": f"event-{index}",
        "timestamp": BASE + timedelta(seconds=index),
        "project_id": "project-a",
        "task_id": "thread:task-a",
        "event_name": "operation",
    }
    values.update(changes)
    return DetectorEvent(**values)  # type: ignore[arg-type]


def _by_detector(events: list[DetectorEvent], **kwargs: object) -> dict[str, list[object]]:
    result: dict[str, list[object]] = {}
    for observation in DetectorEngine().detect(events, **kwargs):  # type: ignore[arg-type]
        result.setdefault(observation.detector_id, []).append(observation)
    return result


def test_explicit_failure_predicates_do_not_conflate_missing_boolean_with_false() -> None:
    events = [
        _event(1, event_name="codex.tool_result", success_string="false"),
        _event(2, event_name="codex.api_request", success_bool=False),
        _event(3, event_name="codex.websocket_request", status_code=503),
        _event(4, event_name="codex.api_request", success_bool=None, status_code=None),
        _event(5, event_name="codex.tool_result", success_string=None),
    ]
    observations = _by_detector(events)
    assert len(observations["tool_failure"]) == 3
    assert len(observations["transport_instability"]) == 2
    failed_ids = {
        event_id
        for observation in observations["tool_failure"]
        for event_id in observation.event_ids  # type: ignore[attr-defined]
    }
    assert failed_ids == {"event-1", "event-2", "event-3"}
    response_observations = _by_detector(
        [_event(6, event_name="codex.api_response", status_code=502)]
    )
    assert len(response_observations["transport_instability"]) == 1


def test_event_detectors_require_their_canonical_event_or_explicit_skill_result() -> None:
    observations = _by_detector(
        [
            _event(1, event_name="codex.sandbox_outcome", outcome="denied"),
            _event(2, event_name="turn/interrupt"),
            _event(3, event_name="turn/steer"),
            _event(4, skill_required="signoz-telemetry", skill_used=False),
            _event(5, skill_required="orchestrate", skill_used=None),
            _event(6, event_name="codex.tool_decision", decision_source="user"),
        ]
    )
    assert len(observations["sandbox_friction"]) == 1
    assert len(observations["turn_correction"]) == 3
    assert len(observations["skill_adherence"]) == 1


def test_repeated_attempt_command_churn_and_tool_loop_are_behavioral_sequences() -> None:
    repeated = _operation("rg", None, "src")
    events = [
        _event(1, operation=repeated),
        _event(2, operation=repeated),
        _event(3, operation=repeated),
        _event(4, operation=_operation("sed", None, "src")),
        _event(5, operation=repeated),
        _event(6, operation=_operation("sed", None, "src")),
        _event(7, operation=_operation("find", None, "src")),
    ]
    observations = _by_detector(events)
    assert observations["repeated_attempt"]
    assert observations["command_churn"]
    assert observations["tool_loop"]
    assert "normalized" in observations["repeated_attempt"][0].membership_explanation  # type: ignore[attr-defined]


def test_tool_loop_uses_operation_order_even_when_context_events_are_interleaved() -> None:
    operations = [
        _operation("one", None, "a"),
        _operation("two", None, "b"),
        _operation("three", None, "c"),
    ]
    observations = _by_detector(
        [
            _event(1, operation=operations[0]),
            _event(2, event_name="turn/start"),
            _event(3, operation=operations[1]),
            _event(4, operation=operations[2]),
            _event(5, operation=operations[0]),
            _event(6, operation=operations[1]),
            _event(7, operation=operations[2]),
        ]
    )
    assert len(observations["tool_loop"]) == 1
    assert observations["tool_loop"][0].event_ids == (  # type: ignore[attr-defined]
        "event-1",
        "event-3",
        "event-4",
        "event-5",
        "event-6",
        "event-7",
    )


def test_quality_gate_bypass_requires_failure_mutation_and_same_command_pass() -> None:
    failed = _operation("pytest", None, "tests", exit_code=1)
    passed = _operation("pytest", None, "tests", exit_code=0)
    changed_command = _operation("pytest", "-q", "tests", exit_code=0)
    mutation = _operation("apply_patch", None, "src/module.py")
    positive = _by_detector(
        [
            _event(1, operation=failed),
            _event(2, operation=mutation, is_mutation=True),
            _event(3, operation=passed),
        ]
    )
    assert len(positive["quality_gate_bypass"]) == 1
    no_same_pass = _by_detector(
        [
            _event(1, operation=failed),
            _event(2, operation=mutation, is_mutation=True),
            _event(3, operation=changed_command),
        ]
    )
    assert "quality_gate_bypass" not in no_same_pass


def test_scope_recurrence_counts_canonical_tasks_but_not_episode_identities() -> None:
    target = _operation("read", None, "src/module.py")
    observations = _by_detector(
        [
            _event(1, operation=target),
            _event(2, operation=target, task_id="thread:task-b"),
            _event(
                3,
                operation=target,
                task_id="episode:trace-c",
                counts_as_distinct_task=False,
            ),
        ]
    )
    assert len(observations["scope_recurrence"]) == 1
    only_episodes = _by_detector(
        [
            _event(1, operation=target, task_id="episode:a", counts_as_distinct_task=False),
            _event(2, operation=target, task_id="episode:b", counts_as_distinct_task=False),
        ]
    )
    assert "scope_recurrence" not in only_episodes


def test_token_outlier_requires_twenty_comparable_episodes_and_strictly_exceeds_p95() -> None:
    event = _event(1, token_count=21)
    assert "token_outlier" not in _by_detector([event], token_baselines={"project-a": range(1, 20)})
    observations = _by_detector([event], token_baselines={"project-a": range(1, 21)})
    assert len(observations["token_outlier"]) == 1
    threshold = _event(2, token_count=19)
    assert "token_outlier" not in _by_detector(
        [threshold], token_baselines={"project-a": range(1, 21)}
    )
    tool_outlier = _event(3, tool_call_count=21, task_id="thread:task-a")
    assert (
        len(
            _by_detector(
                [tool_outlier],
                task_baselines={"thread:task-a:tool_calls": range(1, 21)},
            )["token_outlier"]
        )
        == 1
    )


def test_fingerprints_are_deterministic_and_change_with_failure_class() -> None:
    first = _event(1, event_name="codex.api_request", success_bool=False)
    repeat = _by_detector([first])["tool_failure"][0]
    again = _by_detector([first])["tool_failure"][0]
    status = _by_detector([_event(1, event_name="codex.api_request", status_code=503)])[
        "tool_failure"
    ][0]
    assert repeat.fingerprint == again.fingerprint  # type: ignore[attr-defined]
    assert repeat.fingerprint != status.fingerprint  # type: ignore[attr-defined]
    assert repeat.fingerprint_components.detector_version == 1  # type: ignore[attr-defined]

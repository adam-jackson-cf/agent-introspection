from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from experiments.dashboard_prototype import task_execution as execution
from experiments.dashboard_prototype.contracts import EvidenceProvenance, ExperimentResult
from experiments.dashboard_prototype.experiment_live_common import RemoteCalculationPrimitive
from experiments.dashboard_prototype.task_common import (
    TaskExperimentId,
    TaskExperimentProof,
    TaskLiveEvidence,
)


def window() -> execution.ExtractionWindow:
    end = datetime(2026, 9, 2, tzinfo=UTC)
    return execution.ExtractionWindow(end - timedelta(minutes=5), end)


def proof(experiment_id: TaskExperimentId) -> TaskExperimentProof:
    return TaskExperimentProof(
        experiment_id,
        "safe-run",
        ExperimentResult.BLOCKED,
        EvidenceProvenance.FRESH_REAL,
        window().identity(),
        {},
        {"honest": True},
        (),
        ("authority",),
        "blocked without authority",
    )


def evidence() -> tuple[TaskLiveEvidence, ...]:
    availability = TaskLiveEvidence(
        proof(TaskExperimentId.AVAILABILITY),
        (
            RemoteCalculationPrimitive(
                TaskExperimentId.AVAILABILITY,
                window().end,
                1,
                {
                    "producer": "omp",
                    "surface": "omp",
                    "measure": "M2",
                    "route_state": "blocked",
                    "capability": "unavailable",
                    "time_domain": "source",
                    "population": "activity",
                    "redaction_boundary": "aggregate",
                },
                {
                    "route_count": 1,
                    "activity_population": 0,
                    "resolved_count": 0,
                    "unresolved_count": 0,
                },
            ),
        ),
        None,
        {},
    )
    field = TaskLiveEvidence(
        proof(TaskExperimentId.FIELD_AUDIT),
        (
            RemoteCalculationPrimitive(
                TaskExperimentId.FIELD_AUDIT,
                window().end,
                1,
                {
                    "producer": "omp",
                    "surface": "omp",
                    "field": "task_id",
                    "authority_state": "absent",
                },
                {"classification_count": 1},
            ),
        ),
        None,
        {},
    )
    ordered = TaskLiveEvidence(proof(TaskExperimentId.ORDERED_REDUCER), (), None, {})
    terminal = TaskLiveEvidence(proof(TaskExperimentId.TERMINAL_BOUNDARY), (), None, {})
    remote = TaskLiveEvidence(proof(TaskExperimentId.REMOTE_CALCULATION), (), None, {})
    return availability, field, ordered, terminal, remote


def test_blocked_population_reconciles_and_conserves_exact_events() -> None:
    items = evidence()
    primitives = execution.primitive_events(items, "safe-run", window())
    local = execution._local_oracle(tuple(item.proof for item in items), primitives)
    remote = {"E-Task-0": local["E-Task-0"], "E-Task-1": local["E-Task-1"]}
    final = execution._final_proofs(items, remote, primitives)
    results = execution.result_events(final, "safe-run", window())
    assert local == remote
    assert len(primitives) == 2
    assert len(results) == 5
    assert all(item.result is ExperimentResult.BLOCKED for item in final)
    availability_ids = {
        event.event_id
        for event in primitives
        if event.attributes["dashboard.experiment_id"] == "E-Task-0"
    }
    field_ids = {
        event.event_id
        for event in primitives
        if event.attributes["dashboard.experiment_id"] == "E-Task-1"
    }
    primitive_ids = {event.event_id for event in primitives}
    assert set(final[0].evidence_ids) & primitive_ids == availability_ids
    assert set(final[1].evidence_ids) & primitive_ids == field_ids
    assert set(final[4].evidence_ids) & primitive_ids == availability_ids | field_ids
    assert final[0].assertions["remote_calculation_reconciled"] is True
    assert final[1].assertions["remote_calculation_reconciled"] is True
    assert final[4].assertions["classification_primitives_reconciled"] is True
    envelope = execution.RunEnvelope(
        execution.NAMESPACE,
        "safe-run",
        window(),
        final,
        tuple(event.event_id for event in primitives),
        tuple(event.event_id for event in results),
        local,
        {},
        {},
    )
    assert envelope.payload()["cleanup_selector"] == {
        "namespace": execution.NAMESPACE,
        "run_id": "safe-run",
        "event_ids": [event.event_id for event in (*primitives, *results)],
    }
    assert (
        execution.canonical_json(__import__("json").loads(envelope.canonical_json()))
        == envelope.canonical_json()
    )


def test_mismatch_and_unsafe_or_fabricated_candidates_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = evidence()
    primitives = execution.primitive_events(items, "safe-run", window())
    with pytest.raises(execution.TaskExecutionError):
        execution._final_proofs(items, {"E-Task-0": {}, "E-Task-1": {}}, primitives)
    with pytest.raises(execution.TaskExecutionError):
        execution._safe_attributes(items[2], {"producer": "omp"}, {"classification_count": 1})
    with pytest.raises(execution.TaskExecutionError):
        execution._reject_unsafe({"command_output": "no"})
    monkeypatch.setattr(execution, "remote_event_ids", lambda *_: set())
    monkeypatch.setattr(execution, "sleep", lambda _: None)

    class Client:
        def query(self, sql: str, parameters: object) -> list[dict[str, object]]:
            return []

    with pytest.raises(execution.TaskExecutionError):
        execution._verify_ids(
            Client(),
            execution.result_events(tuple(item.proof for item in items), "safe-run", window()),
        )


def test_event_family_namespace_and_cli_argument_validation() -> None:
    events = execution.primitive_events(evidence(), "safe-run", window())
    assert {event.scope for event in events} == {execution.NAMESPACE}
    assert {event.event_name for event in events} == {execution.EVENT_NAME}
    with pytest.raises(execution.TaskExecutionError):
        execution.validate_run_id("bad run")
    with pytest.raises(SystemExit):
        execution.main([])

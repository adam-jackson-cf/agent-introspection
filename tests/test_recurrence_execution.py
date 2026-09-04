from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from experiments.dashboard_prototype import (
    recurrence_execution as execution,
)
from experiments.dashboard_prototype import (
    recurrence_live_finding,
    recurrence_live_intervention,
    recurrence_live_practice,
    recurrence_live_rule,
    recurrence_live_wave_a,
)
from experiments.dashboard_prototype.contracts import EvidenceProvenance, ExperimentResult
from experiments.dashboard_prototype.recurrence_common import (
    RecurrenceExperimentId,
    RecurrenceExperimentProof,
    RecurrenceLiveEvidence,
    RecurrencePrimitive,
)


def window() -> execution.ExtractionWindow:
    end = datetime(2026, 9, 2, tzinfo=UTC)
    return execution.ExtractionWindow(end - timedelta(minutes=5), end)


def source_time_ns() -> int:
    return execution._epoch_ns(window().start) + 1


def proof(experiment: RecurrenceExperimentId) -> RecurrenceExperimentProof:
    return RecurrenceExperimentProof(
        experiment,
        "safe-run",
        ExperimentResult.BLOCKED,
        EvidenceProvenance.FRESH_REAL,
        window().start,
        window().end,
        window().identity(),
        {},
        {"honest": True},
        (f"retained-evidence-{experiment.value}",),
        ("missing_durable_authority",),
        "blocked without durable authority",
    )


def evidence() -> tuple[RecurrenceLiveEvidence, ...]:
    return tuple(
        RecurrenceLiveEvidence(
            proof(experiment),
            (
                RecurrencePrimitive(
                    experiment,
                    window().end,
                    index,
                    {"stable_identity": "m16", "producer": "omp"},
                    {
                        "reducer_counts": (
                            1.0 if experiment is RecurrenceExperimentId.FINDING_PROJECTION else 1
                        )
                    },
                    source_time_ns=source_time_ns(),
                ),
            ),
            None,
        )
        for index, experiment in enumerate(RecurrenceExperimentId)
    )


def test_extract_live_composes_blocked_evidence_with_retained_source_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = execution.LiveProofRequest("safe-run", window().start, window().end)
    extractors = (
        recurrence_live_wave_a,
        recurrence_live_finding,
        recurrence_live_practice,
        recurrence_live_rule,
        recurrence_live_intervention,
    )
    for index, module in enumerate(extractors):
        item = evidence()[index]
        monkeypatch.setattr(module, "extract", lambda connection, request, item=item: item)

    composed = execution.extract_live(sqlite3.connect(":memory:"), request)

    assert tuple(item.proof.experiment_id for item in composed) == tuple(RecurrenceExperimentId)
    assert all(item.proof.result is ExperimentResult.BLOCKED for item in composed)
    assert all(item.proof.evidence_ids for item in composed)


def test_blocked_closure_has_exact_ids_numeric_time_and_five_results() -> None:
    items = evidence()
    primitives = execution.primitive_events(items, "safe-run", window())
    local = execution._local_oracle(primitives)
    proofs = execution._final_proofs(items, local, primitives)
    results = execution.result_events(proofs, "safe-run", window())

    assert len(primitives) == len(RecurrenceExperimentId)
    assert len({event.event_id for event in primitives}) == len(primitives)
    assert len(results) == len(RecurrenceExperimentId)
    assert all(proof.result is ExperimentResult.BLOCKED for proof in proofs)
    assert all("remote_calculation_reconciled" not in proof.assertions for proof in proofs)
    assert all(proof.evidence_ids for proof in proofs)
    assert all(event.timestamp_ns == source_time_ns() for event in primitives)
    assert all(
        event.attributes["dashboard.source_time_ns"] == source_time_ns() for event in primitives
    )
    assert all(
        "source_time_ns" not in next(iter(local[str(event.attributes["dashboard.experiment_id"])]))
        for event in primitives
    )
    assert (
        execution._finding_evidence_bundle(
            execution._FindingEvidenceInputs(
                items,
                proofs,
                primitives,
                results,
                local,
                local,
                {"row_id": execution.A38_ROW_ID, "sql": "SELECT", "parameters": {}},
            )
        )
        is None
    )


def test_a38_only_promotes_the_complete_finding_row() -> None:
    items = list(evidence())
    finding_index = list(RecurrenceExperimentId).index(RecurrenceExperimentId.FINDING_PROJECTION)
    finding = items[finding_index]
    items[finding_index] = replace(
        finding,
        proof=replace(
            finding.proof,
            assertions={
                "schema_required": True,
                "durable_window_bounds_authoritative": True,
                "membership_task_identity_authoritative": True,
                "selected_finding_evidence_range_exact": True,
                "selected_membership_denominator_reconciled": True,
                "latest_activity_versions_global": True,
            },
        ),
        finding_version_source_ids=("finding-version-immutable-1",),
        canonical_task_membership_source_ids=("canonical-membership-immutable-1",),
    )
    primitives = execution.primitive_events(tuple(items), "safe-run", window())
    local = execution._local_oracle(primitives)
    proofs = execution._final_proofs(tuple(items), local, primitives)
    results = execution.result_events(proofs, "safe-run", window())
    bundle = execution._finding_evidence_bundle(
        execution._FindingEvidenceInputs(
            tuple(items),
            proofs,
            primitives,
            results,
            local,
            local,
            {
                "row_id": execution.A38_ROW_ID,
                "sql": "SELECT",
                "parameters": {
                    "start_ns": 1,
                    "end_ns": 2,
                    "run_id_hash": "run",
                    "event_name": execution.EVENT_NAME,
                    "experiment_id": execution.FINDING_EXPERIMENT_ID,
                    "event_0": primitives[0].event_id,
                    "event_1": primitives[1].event_id,
                    "event_2": primitives[2].event_id,
                    "event_3": primitives[3].event_id,
                    "event_4": primitives[4].event_id,
                },
            },
        )
    )

    assert [proof.result for proof in proofs] == [
        ExperimentResult.BLOCKED,
        ExperimentResult.PROVEN,
        ExperimentResult.BLOCKED,
        ExperimentResult.BLOCKED,
        ExperimentResult.BLOCKED,
    ]
    assert bundle is not None
    assert bundle["row_id"] == "A38"
    source = cast(dict[str, tuple[str, ...]], bundle["source"])
    remote = cast(dict[str, object], bundle["remote"])
    oracle = cast(dict[str, object], bundle["oracle"])
    reducer = cast(dict[str, tuple[str, ...]], bundle["reducer"])
    delivery = cast(dict[str, tuple[str, ...]], bundle["delivery"])
    assert source["finding_version_ids"] == ("finding-version-immutable-1",)
    assert source["canonical_task_membership_ids"] == ("canonical-membership-immutable-1",)
    assert remote["sql"] == "SELECT"
    assert set(cast(dict[str, object], remote["parameters"])) == {
        "start_ns",
        "end_ns",
        "run_id_hash",
        "event_name",
        "experiment_id",
        "event_0",
        "event_1",
        "event_2",
        "event_3",
        "event_4",
    }
    assert remote["scalars"] == oracle["scalars"]
    assert remote["scalars"] == (
        {
            "dimension": next(iter(local[execution.FINDING_EXPERIMENT_ID])),
            "measure": "reducer_counts",
            "value": 1.0,
            "declared_type": "Float64",
        },
    )
    assert len(reducer["event_ids"]) == 1
    assert len(delivery["event_ids"]) == 1


def test_a38_stays_blocked_without_each_immutable_source_binding() -> None:
    items = list(evidence())
    finding_index = list(RecurrenceExperimentId).index(RecurrenceExperimentId.FINDING_PROJECTION)
    finding = items[finding_index]
    items[finding_index] = replace(
        finding,
        proof=replace(
            finding.proof,
            assertions=dict.fromkeys(execution.FINDING_ROW_GATE_ASSERTIONS, True),
        ),
        finding_version_source_ids=("finding-version-immutable-1",),
    )

    primitives = execution.primitive_events(tuple(items), "safe-run", window())
    proofs = execution._final_proofs(tuple(items), execution._local_oracle(primitives), primitives)

    assert proofs[finding_index].result is ExperimentResult.BLOCKED


def test_a38_stays_blocked_for_equal_numeric_values_with_different_types() -> None:
    items = list(evidence())
    finding_index = list(RecurrenceExperimentId).index(RecurrenceExperimentId.FINDING_PROJECTION)
    finding = items[finding_index]
    items[finding_index] = replace(
        finding,
        proof=replace(
            finding.proof,
            assertions=dict.fromkeys(execution.FINDING_ROW_GATE_ASSERTIONS, True),
        ),
        finding_version_source_ids=("finding-version-immutable-1",),
        canonical_task_membership_source_ids=("canonical-membership-immutable-1",),
        primitives=(replace(finding.primitives[0], measures={"reducer_counts": 1}),),
    )

    primitives = execution.primitive_events(tuple(items), "safe-run", window())
    local = execution._local_oracle(primitives)
    proofs = execution._final_proofs(tuple(items), local, primitives)
    results = execution.result_events(proofs, "safe-run", window())

    assert proofs[finding_index].result is ExperimentResult.BLOCKED
    assert (
        execution._finding_evidence_bundle(
            execution._FindingEvidenceInputs(
                tuple(items),
                proofs,
                primitives,
                results,
                local,
                local,
                {
                    "row_id": execution.A38_ROW_ID,
                    "sql": "SELECT",
                    "parameters": {
                        "start_ns": 1,
                        "end_ns": 2,
                        "run_id_hash": "run",
                        "event_name": execution.EVENT_NAME,
                        "experiment_id": execution.FINDING_EXPERIMENT_ID,
                        **{
                            f"event_{index}": event.event_id
                            for index, event in enumerate(primitives)
                        },
                    },
                },
            )
        )
        is None
    )


def test_primitive_uses_datetime_when_exact_source_time_is_unavailable() -> None:
    item = RecurrenceLiveEvidence(
        proof(RecurrenceExperimentId.WAVE_A),
        (
            RecurrencePrimitive(
                RecurrenceExperimentId.WAVE_A,
                window().end,
                0,
                {"stable_identity": "m16", "producer": "omp"},
                {"reducer_counts": 1},
            ),
        ),
        None,
    )

    (event,) = execution.primitive_events((item,), "safe-run", window())

    assert event.timestamp_ns == execution._epoch_ns(window().end)
    assert event.attributes["dashboard.source_time_ns"] == event.timestamp_ns


def test_remote_mismatch_aborts_before_result_emission(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database = tmp_path / "state.sqlite"
    sqlite3.connect(database).close()
    emitted: list[tuple[str, ...]] = []
    config = cast(
        Any,
        SimpleNamespace(
            database=SimpleNamespace(path=database),
            signoz=SimpleNamespace(
                otlp_http_endpoint="http://127.0.0.1:4318",
                docker_context="unused",
                clickhouse_container="unused",
            ),
        ),
    )
    monkeypatch.setattr(execution, "extract_live", lambda connection, request: evidence())
    monkeypatch.setattr(execution, "ClickHouseClient", lambda **kwargs: object())
    monkeypatch.setattr(
        execution,
        "enqueue_events",
        lambda connection, events: emitted.append(tuple(event.event_id for event in events)),
    )
    monkeypatch.setattr(
        execution,
        "_exact_drain",
        lambda connection, events, endpoint: {
            "selected": len(events),
            "delivered": len(events),
            "pending": 0,
        },
    )
    monkeypatch.setattr(execution, "_verify_ids", lambda client, events: None)
    monkeypatch.setattr(
        execution,
        "remote_calculations",
        lambda client, primitives, extract_window, run_id: ({}, {}),
    )
    monkeypatch.setattr(execution, "validate_output_path", lambda output: output)

    with pytest.raises(execution.RecurrenceExecutionError, match="reconciliation mismatch"):
        execution.run(
            run_id="safe-run",
            start=window().start,
            end=window().end,
            output=tmp_path / "proof.json",
            config=config,
        )

    assert len(emitted) == 1
    assert len(emitted[0]) == len(RecurrenceExperimentId)


def test_conflicting_result_payload_fails_after_exact_id_delivery() -> None:
    proofs = execution._final_proofs(
        evidence(),
        execution._local_oracle(execution.primitive_events(evidence(), "safe-run", window())),
        execution.primitive_events(evidence(), "safe-run", window()),
    )
    results = execution.result_events(proofs, "safe-run", window())
    event = results[0]

    class Client:
        def query(self, sql: str, parameters: object) -> list[dict[str, object]]:
            del sql, parameters
            row: dict[str, object] = {
                "event_id": event.event_id,
                "result": event.attributes["dashboard.result"],
                "proof_hash": event.attributes["dashboard.proof_hash"],
                "experiment_id": event.attributes["dashboard.experiment_id"],
                "run_id_hash": event.attributes["dashboard.run_id_hash"],
                "namespace": execution.NAMESPACE,
                "event_name": execution.EVENT_NAME,
            }
            return [row, {**row, "result": "Proven"}]

    with pytest.raises(execution.RecurrenceExecutionError, match="result payload"):
        execution._verify_result_payloads(Client(), (event,))


def test_pre_epoch_window_never_enqueues(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    enqueued: list[object] = []
    monkeypatch.setattr(execution, "validate_output_path", lambda output: output)
    monkeypatch.setattr(
        execution, "enqueue_events", lambda connection, events: enqueued.append(events)
    )
    config = cast(
        Any,
        SimpleNamespace(
            database=SimpleNamespace(path=tmp_path / "unused.sqlite"),
            signoz=SimpleNamespace(otlp_http_endpoint="http://127.0.0.1:4318"),
        ),
    )

    with pytest.raises(execution.RecurrenceExecutionError, match="Unix epoch"):
        execution.run(
            run_id="safe-run",
            start=datetime(1969, 12, 31, tzinfo=UTC),
            end=datetime(1970, 1, 1, tzinfo=UTC),
            output=tmp_path / "proof.json",
            config=config,
        )

    assert not enqueued


def test_remote_float64_scalar_normalizes_json_integral_encoding() -> None:
    primitives = execution.primitive_events(evidence(), "safe-run", window())
    finding_primitive = primitives[
        list(RecurrenceExperimentId).index(RecurrenceExperimentId.FINDING_PROJECTION)
    ]

    class Client:
        def query(self, sql: str, parameters: object) -> list[dict[str, object]]:
            del sql, parameters
            return [
                {
                    "experiment_id": execution.FINDING_EXPERIMENT_ID,
                    "dimension_json": finding_primitive.attributes["dashboard.dimension_json"],
                    "measure": "reducer_counts",
                    "value": 1,
                    "value_type": "Float64",
                }
            ]

    remote, _ = execution.remote_calculations(Client(), primitives, window(), "safe-run")

    assert remote[execution.FINDING_EXPERIMENT_ID] == {
        str(finding_primitive.attributes["dashboard.dimension_json"]): {"reducer_counts": 1.0}
    }


def test_conflicting_remote_duplicate_tuple_fails() -> None:
    primitives = execution.primitive_events(evidence(), "safe-run", window())
    dimension = str(primitives[0].attributes["dashboard.dimension_json"])
    captured: dict[str, object] = {}

    class Client:
        def query(self, sql: str, parameters: object) -> list[dict[str, object]]:
            captured["sql"] = sql
            captured["parameters"] = parameters
            return [
                {
                    "experiment_id": "E-Recurrence-0",
                    "dimension_json": dimension,
                    "measure": "reducer_counts",
                    "value": 1.0,
                    "value_type": "Float64",
                },
                {
                    "experiment_id": "E-Recurrence-0",
                    "dimension_json": dimension,
                    "measure": "reducer_counts",
                    "value": 1.0,
                    "value_type": "Float64",
                },
            ]

    with pytest.raises(execution.RecurrenceExecutionError, match="conflicting remote duplicate"):
        execution.remote_calculations(Client(), primitives, window(), "safe-run")

    assert "WHERE timestamp > {start_ns:UInt64} AND timestamp <= {end_ns:UInt64}" in str(
        captured["sql"]
    )
    assert "dashboard.experiment_id'] = {experiment_id:String}" in str(captured["sql"])
    assert captured["parameters"] == {
        "start_ns": execution._epoch_ns(window().start),
        "end_ns": execution._epoch_ns(window().end),
        "run_id_hash": execution.canonical_hash("safe-run"),
        "event_name": execution.EVENT_NAME,
        "experiment_id": execution.FINDING_EXPERIMENT_ID,
        **{f"event_{index}": event.event_id for index, event in enumerate(primitives)},
    }


def test_privacy_boundary_rejects_raw_session_values() -> None:
    with pytest.raises(execution.RecurrenceExecutionError, match="unsafe evidence"):
        execution.canonical_json({"native_session_id": "private"})

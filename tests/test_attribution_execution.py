from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_introspection.config import AppConfig, DatabaseConfig, SigNozConfig
from experiments.dashboard_prototype import attribution_execution as execution
from experiments.dashboard_prototype.attribution_common import (
    AttributionExperimentId,
    AttributionExperimentProof,
    AttributionRowEvidence,
    AttributionRowState,
)
from experiments.dashboard_prototype.attribution_live_common import (
    AttributionCalculationPrimitive,
    AttributionLiveEvidence,
    LiveProofRequest,
)
from experiments.dashboard_prototype.contracts import (
    EvidenceProvenance,
    ExperimentResult,
)


def ready_row_payload() -> dict[str, object]:
    native_session_id = "native-1"
    event_id_inputs = {
        stage: {
            "experiment_id": AttributionExperimentId.BASELINE.value,
            "row_id": "A07",
            "producer": "omp",
            "native_session_id": native_session_id,
            "source_id": "source-immutable",
            "reducer_id": "reducer-immutable",
            "entity_version": 2,
            "event_id_ordinal": ordinal,
        }
        for ordinal, stage in enumerate(("source", "reducer", "delivery"))
    }
    row = AttributionRowEvidence(
        AttributionExperimentId.BASELINE,
        "A07",
        "omp",
        native_session_id,
        AttributionRowState.READY,
        None,
        event_id_inputs,
        {stage: f"delivered-{stage}" for stage in event_id_inputs},
        {"count": 1},
        {"count": "integer"},
        {"count": 1},
        {"count": "integer"},
        {"count": 1},
        {"count": "integer"},
        {"count": 1},
        {"count": "integer"},
    )
    return execution._row_payload(row)


def window() -> execution.ExtractionWindow:
    end = datetime(2026, 9, 1, tzinfo=UTC)
    return execution.ExtractionWindow(end - timedelta(minutes=5), end)


def proof(
    experiment: AttributionExperimentId, result: ExperimentResult = ExperimentResult.BLOCKED
) -> AttributionExperimentProof:
    return AttributionExperimentProof(
        experiment,
        "safe-run",
        result,
        EvidenceProvenance.FRESH_REAL,
        window().identity(),
        {},
        {"bounded": True},
        (),
        ("source",) if result is ExperimentResult.BLOCKED else (),
        "proposal",
    )


def item(
    experiment: AttributionExperimentId,
    dimensions: dict[str, str | int | bool],
    measures: dict[str, int | float],
    query: str,
) -> AttributionLiveEvidence:
    return AttributionLiveEvidence(
        proof(experiment),
        (AttributionCalculationPrimitive(experiment, window().end, 1, dimensions, measures),),
        query,
        {},
    )


def test_namespace_order_and_validation() -> None:
    assert execution.NAMESPACE == "agent-introspection.dashboard-prototype.v1"
    assert execution.EVENT_NAME == "dashboard_prototype.attribution_snapshot.v1"
    assert tuple(f"E-Attribution-{n}" for n in range(1, 6)) == execution.EXPERIMENT_IDS
    with pytest.raises(execution.AttributionExecutionError):
        execution.validate_run_id("../unsafe")
    with pytest.raises(execution.AttributionExecutionError):
        execution.validate_loopback_endpoint("https://localhost:4318")
    with pytest.raises(execution.AttributionExecutionError):
        execution.validate_output_path(Path("/tmp/output.json"))


def test_row_obligations_are_independent_of_aggregate_event_ids() -> None:
    rows = execution.row_obligations(
        (
            AttributionLiveEvidence(proof(AttributionExperimentId.BASELINE), (), None, {}),
            AttributionLiveEvidence(proof(AttributionExperimentId.CODEX_APP_SERVER), (), None, {}),
        ),
        (),
    )

    assert len(rows) == 9
    assert len({row.deterministic_id() for row in rows}) == 9
    assert all(row.state.value == "Blocked" and row.event_ids is None for row in rows)


def test_e2_aggregate_does_not_promote_app_server_row_obligations() -> None:
    app_server = AttributionLiveEvidence(
        proof(AttributionExperimentId.CODEX_APP_SERVER, ExperimentResult.PROVEN),
        tuple(
            AttributionCalculationPrimitive(
                AttributionExperimentId.CODEX_APP_SERVER,
                window().end,
                ordinal,
                {"scenario": scenario, "accepted": True},
                {"count": 1},
            )
            for ordinal, scenario in enumerate(("startup", "resume", "clear", "compact"), start=1)
        ),
        "aggregate-e2",
        {},
    )
    rows = execution.row_obligations(
        (
            AttributionLiveEvidence(proof(AttributionExperimentId.BASELINE), (), None, {}),
            app_server,
        ),
        (),
    )

    app_server_rows = [row for row in rows if row.producer == "codex-app-server"]
    assert [(row.row_id, row.state, row.blocked_reason) for row in app_server_rows] == [
        ("A07", AttributionRowState.BLOCKED, "no_row_authority"),
        ("A08", AttributionRowState.BLOCKED, "no_row_authority"),
        ("A09", AttributionRowState.BLOCKED, "no_row_authority"),
    ]
    assert all(row.native_session_id is None and row.event_ids is None for row in app_server_rows)
    envelope = execution.RunEnvelope(
        execution.NAMESPACE,
        "safe-run",
        window(),
        (),
        (),
        (),
        {},
        {},
        {},
        rows,
    )
    assert len(json.loads(envelope.canonical_json())["row_obligations"]) == 9


def test_native_lineage_is_serializable_only_in_a_canonical_top_level_row_obligation() -> None:
    row = ready_row_payload()

    envelope = execution.RunEnvelope(
        execution.NAMESPACE,
        "safe-run",
        window(),
        (),
        (),
        (),
        {},
        {},
        {},
        (execution._canonical_row_evidence(row),),
    )
    serialized = envelope.canonical_json()
    assert json.loads(serialized)["row_obligations"][0]["native_session_id"] == "native-1"
    assert {
        input_["native_session_id"]
        for input_ in json.loads(serialized)["row_obligations"][0]["event_id_inputs"].values()
    } == {"native-1"}
    with pytest.raises(execution.AttributionExecutionError, match="unsafe evidence field"):
        execution.canonical_json({"native_session_id": "native-1"})
    with pytest.raises(execution.AttributionExecutionError, match="top-level"):
        execution.canonical_json({"row_obligations": [row]})


@pytest.mark.parametrize(
    "mutate",
    [
        lambda row: row.pop("event_ids"),
        lambda row: row.update({"unexpected": "identity"}),
        lambda row: row["event_id_inputs"]["delivery"].update({"native_session_id": "other"}),
        lambda row: row["event_id_inputs"]["source"].update({"transcript": "prohibited"}),
        lambda row: row.update({"deterministic_id": "forged"}),
    ],
)
def test_row_obligation_serialization_rejects_noncanonical_or_mismatched_lineage(
    mutate: Callable[[dict[str, object]], object],
) -> None:
    row = ready_row_payload()
    mutate(row)

    with pytest.raises(execution.AttributionExecutionError):
        execution._canonical_envelope_json({"row_obligations": [row]})


@pytest.mark.parametrize("field", ["prompt", "response", "transcript", "path", "secret"])
def test_prohibited_evidence_fields_remain_rejected(field: str) -> None:
    with pytest.raises(execution.AttributionExecutionError, match="unsafe evidence field"):
        execution.canonical_json({field: "prohibited"})


def test_blocked_codex_app_server_evidence_has_no_synthetic_inputs() -> None:
    end = datetime(2026, 9, 1, tzinfo=UTC)
    evidence = execution._blocked_codex_app_server_evidence(
        LiveProofRequest("safe-run", end - timedelta(minutes=5), end)
    )

    assert evidence.proof.experiment_id is AttributionExperimentId.CODEX_APP_SERVER
    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.provenance is EvidenceProvenance.FRESH_REAL
    assert evidence.primitives == ()
    assert evidence.remote_query_id is None
    assert evidence.remote_oracle == {}
    assert evidence.proof.blocked_boundaries == (
        "startup-scenario-source-authority",
        "resume-scenario-source-authority",
        "clear-scenario-source-authority",
        "compact-scenario-source-authority",
    )
    assert evidence.proof.assertions["separate_codex_app_excluded"] is True


def test_remote_calculation_shapes() -> None:
    e1 = execution.primitive_events(
        (
            item(
                AttributionExperimentId.BASELINE,
                {
                    "primitive_kind": "p5",
                    "producer": "omp",
                    "surface": "omp",
                    "direction": "source_to_lifecycle",
                    "matched": True,
                },
                {"count": 1},
                "e1",
            ),
        ),
        "safe-run",
        window(),
    )
    e2 = execution.primitive_events(
        (
            item(
                AttributionExperimentId.CODEX_APP_SERVER,
                {"scenario": "fresh", "accepted": True},
                {"count": 1},
                "e2",
            ),
        ),
        "safe-run",
        window(),
    )
    e4 = execution.primitive_events(
        (
            item(
                AttributionExperimentId.LIFECYCLE_DELAY,
                {"cohort": "safe", "matched": True},
                {"lifecycle_delay_seconds": 1.0, "negative_skew": 0},
                "e4",
            ),
        ),
        "safe-run",
        window(),
    )
    e5 = execution.primitive_events(
        (
            item(
                AttributionExperimentId.LATE_CONTEXT,
                {
                    "producer": "omp",
                    "surface": "omp",
                    "method": "context",
                    "prior_reason": "none",
                    "resolved_event_digest": "a" * 64,
                },
                {"transition_count": 1},
                "e5",
            ),
        ),
        "safe-run",
        window(),
    )
    assert e1[0].attributes["dashboard.matched"] == "true"
    assert e2[0].attributes["dashboard.accepted"] == "true"
    assert e4[0].attributes["dashboard.matched"] == "true"
    assert e5[0].attributes["dashboard.resolved_event_digest"] == "a" * 64

    class Client:
        def query(self, sql: str, parameters: object) -> list[dict[str, object]]:
            if "E-Attribution-1" in sql:
                return [
                    {
                        "primitive_kind": "p5",
                        "producer": "omp",
                        "surface": "omp",
                        "direction": "source_to_lifecycle",
                        "matched": "true",
                        "outcome": "",
                        "method": "",
                        "diagnostic": "",
                        "project_digest": "none",
                        "count": 1,
                    }
                ]
            if "E-Attribution-2" in sql:
                return [{"scenario": "fresh", "scenario_population": 1, "accepted_count": 1}]
            if "E-Attribution-4" in sql:
                return [
                    {
                        "cohort": "safe",
                        "selected_sessions": 1,
                        "matched_sessions": 1,
                        "negative_skew_sessions": 0,
                        "n": 1,
                        "p50_lifecycle_delay_seconds": 1.0,
                        "p95_lifecycle_delay_seconds": 1.0,
                    }
                ]
            return [
                {
                    "cohort": e5[0].attributes["dashboard.cohort"],
                    "resolved_event_digest": e5[0].attributes["dashboard.resolved_event_digest"],
                    "transition_count": 1,
                }
            ]

    result = execution.remote_calculations(Client(), e1 + e2 + e4 + e5, window(), "safe-run")
    assert set(result) == {
        "E-Attribution-1",
        "E-Attribution-2",
        "E-Attribution-4",
        "E-Attribution-5",
    }


def test_e1_remote_reducer_accumulates_p8_across_distinct_projects() -> None:
    dimensions = {
        "primitive_kind": "p7_p8",
        "producer": "omp",
        "surface": "omp",
        "direction": "none",
        "matched": False,
        "outcome": "attributed",
        "method": "context",
        "diagnostic": "exact",
    }
    first = item(
        AttributionExperimentId.BASELINE,
        {**dimensions, "project_digest": "a" * 64},
        {"count": 1},
        "e1",
    )
    second = item(
        AttributionExperimentId.BASELINE,
        {**dimensions, "project_digest": "b" * 64},
        {"count": 1},
        "e1",
    )
    expected = {
        "p7": {"eligible": 2, "attributed": 2, "unresolved": 0, "distinct_projects": 2},
        "p8": {"total": 2},
        "p7.omp.omp": {
            "eligible": 2,
            "attributed": 2,
            "unresolved": 0,
            "distinct_projects": 2,
        },
        "p8.omp.omp.context.attributed.exact": {"count": 2},
    }
    evidence = AttributionLiveEvidence(
        proof(AttributionExperimentId.BASELINE),
        first.primitives + second.primitives,
        "e1",
        expected,
    )
    events = execution.primitive_events((evidence,), "safe-run", window())

    class Client:
        def query(self, sql: str, parameters: object) -> list[dict[str, object]]:
            assert "GROUP BY primitive_kind" in sql
            return [
                {
                    **dimensions,
                    "matched": "false",
                    "project_digest": digest,
                    "count": 1,
                }
                for digest in ("a" * 64, "b" * 64)
            ]

    remote = execution.remote_calculations(Client(), events, window(), "safe-run")
    assert remote["E-Attribution-1"] == expected
    finalized = execution._final_proofs((evidence,), remote)[0]
    assert finalized.result is ExperimentResult.BLOCKED
    assert finalized.assertions["remote_calculation_reconciled"] is True


def test_blocked_matching_remote_stays_blocked_and_mismatch_fails() -> None:
    blocked = AttributionLiveEvidence(
        proof(AttributionExperimentId.LIFECYCLE_DELAY), (), "e4", {"safe": {"n": 0}}
    )
    final = execution._final_proofs((blocked,), {"E-Attribution-4": {"safe": {"n": 0}}})[0]
    assert final.result is ExperimentResult.BLOCKED
    assert final.assertions["remote_calculation_reconciled"] is True
    failed = execution._final_proofs((blocked,), {"E-Attribution-4": {"safe": {"n": 1}}})[0]
    assert failed.result is ExperimentResult.FAILED


def test_visibility_retries_and_cleanup_is_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    events = execution.result_events(
        (proof(AttributionExperimentId.CLAUDE_BOUNDARY),), "safe-run", window()
    )
    seen = iter((set(), {events[0].event_id}))
    delays: list[float] = []
    monkeypatch.setattr(execution, "remote_event_ids", lambda *_: next(seen))
    monkeypatch.setattr(execution, "sleep", delays.append)

    class Client:
        def query(self, sql: str, parameters: object) -> list[dict[str, object]]:
            return []

    execution._verify_ids(Client(), events)
    assert delays == [0.5]
    envelope = execution.RunEnvelope(
        execution.NAMESPACE,
        "safe-run",
        window(),
        (),
        ("primitive",),
        ("result",),
        {},
        {},
        {},
        (),
    )
    assert envelope.payload()["row_obligations"] == []
    cleanup = envelope.payload()["cleanup_selector"]
    assert isinstance(cleanup, dict)
    assert cleanup["event_ids"] == ["primitive", "result"]


def test_e3_has_no_primitives() -> None:
    assert (
        execution.primitive_events(
            (
                AttributionLiveEvidence(
                    proof(AttributionExperimentId.CLAUDE_BOUNDARY), (), None, {}
                ),
            ),
            "safe-run",
            window(),
        )
        == ()
    )


def test_mismatch_aborts_before_result_enqueue_or_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database = tmp_path / "state.sqlite"
    sqlite3.connect(database).close()
    source = item(
        AttributionExperimentId.CODEX_APP_SERVER,
        {"scenario": "fresh", "accepted": True},
        {"count": 1},
        "e2",
    )
    source = AttributionLiveEvidence(
        source.proof,
        source.primitives,
        source.remote_query_id,
        {"fresh": {"accepted_count": 1, "scenario_population": 1}},
    )
    monkeypatch.setattr(execution, "extract_live", lambda *_: (source,))
    monkeypatch.setattr(execution, "ClickHouseClient", lambda **_: object())
    monkeypatch.setattr(execution, "_exact_drain", lambda *_: {"selected": 1, "delivered": 1})
    monkeypatch.setattr(execution, "_verify_ids", lambda *_: None)
    monkeypatch.setattr(
        execution,
        "remote_calculations",
        lambda *_: {"E-Attribution-2": {"fresh": {"accepted_count": 0, "scenario_population": 1}}},
    )
    enqueued: list[object] = []
    monkeypatch.setattr(execution, "enqueue_events", lambda _, events: enqueued.append(events))
    config = AppConfig(
        database=DatabaseConfig(path=database),
        signoz=SigNozConfig(otlp_http_endpoint="http://localhost:4318"),
    )
    output = tmp_path / "evidence" / "never.json"
    monkeypatch.setattr(execution, "validate_output_path", lambda _: output)
    with pytest.raises(execution.AttributionExecutionError, match="reconciliation"):
        execution.run(
            run_id="safe-run",
            start=window().start,
            end=window().end,
            output=output,
            config=config,
        )
    assert len(enqueued) == 1
    assert not output.exists()


def test_e5_digest_mismatch_fails_reconciliation() -> None:
    evidence = AttributionLiveEvidence(
        proof(AttributionExperimentId.LATE_CONTEXT),
        (
            AttributionCalculationPrimitive(
                AttributionExperimentId.LATE_CONTEXT,
                window().end,
                1,
                {
                    "producer": "omp",
                    "surface": "omp",
                    "method": "context",
                    "prior_reason": "none",
                    "resolved_event_digest": "a" * 64,
                },
                {"transition_count": 1},
            ),
        ),
        "e5",
        {"local": {"transition_count": 1}},
    )
    expected = execution._expected_oracle(evidence)
    cohort = next(iter(expected))
    actual = {
        "E-Attribution-5": {
            cohort: {
                "transition_count": 1,
                "resolved_event_digests": {"substituted-digest": 1},
            }
        }
    }
    assert execution._final_proofs((evidence,), actual)[0].result is ExperimentResult.FAILED

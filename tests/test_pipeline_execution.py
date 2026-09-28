from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from agent_introspection.config import AppConfig, DatabaseConfig, SigNozConfig
from agent_introspection.telemetry import DerivedEvent
from experiments.dashboard_prototype import pipeline_execution as execution
from experiments.dashboard_prototype.contracts import EvidenceProvenance, ExperimentResult
from experiments.dashboard_prototype.pipeline_common import (
    PipelineExperimentId,
    PipelineExperimentProof,
)
from experiments.dashboard_prototype.pipeline_execution import (
    EVENT_NAME,
    EXPERIMENT_IDS,
    NAMESPACE,
    ExtractionWindow,
    PipelineExecutionError,
    RunEnvelope,
    _exact_drain,
    _final_proofs,
    _params,
    _reject_unsafe,
    primitive_events,
    remote_calculations,
    result_events,
    run,
    validate_loopback_endpoint,
    validate_output_path,
    validate_run_id,
)
from experiments.dashboard_prototype.pipeline_live_common import (
    LiveExperimentEvidence,
    LiveProofRequest,
    RemoteCalculationPrimitive,
)
from experiments.dashboard_prototype.pipeline_live_outbox import (
    E5DeliveryAttemptPrimitive,
    E5FinalDrainPrimitive,
    E5OutboxEventPrimitive,
    OutboxEventStatus,
)
from experiments.dashboard_prototype.pipeline_outbox import DeliveryAttemptStatus


@pytest.fixture
def connection() -> Iterator[sqlite3.Connection]:
    database = sqlite3.connect(":memory:")
    try:
        yield database
    finally:
        database.close()


def window() -> ExtractionWindow:
    end = datetime(2026, 9, 1, tzinfo=UTC)
    return ExtractionWindow(end - timedelta(minutes=5), end)


def proof(
    experiment: PipelineExperimentId, result: ExperimentResult = ExperimentResult.BLOCKED
) -> PipelineExperimentProof:
    return PipelineExperimentProof(
        experiment,
        "safe-run",
        result,
        EvidenceProvenance.FRESH_REAL,
        window().identity(),
        {"population": 1},
        {"bounded": result is not ExperimentResult.FAILED},
        ("immutable-source-id",) if result is ExperimentResult.PROVEN else (),
        ("source",) if result is ExperimentResult.BLOCKED else (),
        "proposal",
    )


def test_canonical_namespace_family_and_validators() -> None:
    assert NAMESPACE == "agent-introspection.dashboard-prototype.v1"
    assert EVENT_NAME == "dashboard_prototype.pipeline_snapshot.v1"
    with pytest.raises(PipelineExecutionError):
        validate_run_id("../bad")
    with pytest.raises(PipelineExecutionError):
        validate_loopback_endpoint("https://localhost:4318")
    with pytest.raises(PipelineExecutionError):
        validate_output_path(Path("/tmp/evidence.json"))


def test_primitive_and_result_events_are_deterministic_and_allowlisted() -> None:
    item = LiveExperimentEvidence(
        proof(PipelineExperimentId.SOURCE_LAG, ExperimentResult.PROVEN),
        (
            RemoteCalculationPrimitive(
                PipelineExperimentId.SOURCE_LAG,
                window().end,
                1,
                {"disposition": "accepted"},
                {"lag_seconds": 1.0, "negative_skew": 0},
            ),
        ),
        "pipeline-source-lag-v1",
    )
    first = primitive_events((item,), "safe-run", window())
    assert first == primitive_events((item,), "safe-run", window())
    assert first[0].attributes["dashboard.event_kind"] == "primitive"
    results = result_events((item.proof,), "safe-run", window())
    with pytest.raises(PipelineExecutionError, match="authority population"):
        RunEnvelope(
            NAMESPACE,
            "safe-run",
            window(),
            (item.proof,),
            tuple(event.event_id for event in first),
            tuple(event.event_id for event in results),
            {},
            {},
            {
                "primitive_selected": 1,
                "primitive_delivered": 1,
                "result_selected": 1,
                "result_delivered": 1,
            },
        )


def test_primitive_events_preserve_semantic_source_time_and_bucket_units() -> None:
    source_time = window().start + timedelta(seconds=1)
    item = LiveExperimentEvidence(
        proof(PipelineExperimentId.SOURCE_LAG, ExperimentResult.PROVEN),
        (
            RemoteCalculationPrimitive(
                PipelineExperimentId.SOURCE_LAG,
                source_time,
                1,
                {"cohort": "cohort-1", "disposition": "accepted"},
                {"lag_seconds": 1.0, "negative_skew": 0},
            ),
        ),
        "pipeline-source-lag-v1",
    )
    events = primitive_events((item,), "safe-run", window())
    assert events[0].timestamp_ns == int(source_time.timestamp() * 1_000_000_000)
    _, parameters = _params(events, window(), "pipeline-source-lag-v1", "safe-run")
    assert parameters["start_bucket"] == int(window().start.timestamp()) - 1_800
    assert parameters["end_bucket"] == int(window().end.timestamp())


def test_exact_drain_rejects_presence_or_partial_delivery(
    monkeypatch: pytest.MonkeyPatch, connection: sqlite3.Connection
) -> None:
    event = result_events((proof(PipelineExperimentId.SNAPSHOT),), "safe-run", window())
    monkeypatch.setattr(
        "experiments.dashboard_prototype.pipeline_execution.drain_outbox_event_ids",
        lambda *args, **kwargs: {"selected": 1, "delivered": 0, "pending": 1},
    )
    with pytest.raises(PipelineExecutionError, match="exact outbox drain mismatch"):
        _exact_drain(connection, event, "http://localhost:4318")


def test_exact_remote_verification_waits_for_visibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Client:
        def query(self, sql: str, parameters: Mapping[str, str | int]) -> list[dict[str, Any]]:
            raise AssertionError("remote verification is mocked")

    events = result_events((proof(PipelineExperimentId.SNAPSHOT),), "safe-run", window())
    expected = {events[0].event_id}
    observed = iter((set(), expected))
    delays: list[float] = []
    monkeypatch.setattr(execution, "remote_event_ids", lambda *_: next(observed))
    monkeypatch.setattr(execution, "sleep", delays.append)

    execution._verify_ids(Client(), events)

    assert delays == [0.5]


def test_unsafe_nested_fields_reject_but_cleanup_selector_is_allowed() -> None:
    _reject_unsafe({"cleanup_selector": {"event_ids": ["safe"]}})
    with pytest.raises(PipelineExecutionError):
        _reject_unsafe({"nested": {"native_session_id": "unsafe"}})


def test_unsupported_proven_proof_downgrades() -> None:
    proven = PipelineExperimentProof(
        PipelineExperimentId.SNAPSHOT,
        "safe-run",
        ExperimentResult.PROVEN,
        EvidenceProvenance.FRESH_REAL,
        window().identity(),
        {},
        {"bounded": True},
        (),
        (),
        "proposal",
    )
    primitive = RemoteCalculationPrimitive(
        PipelineExperimentId.SNAPSHOT,
        window().end,
        1,
        {"kind": "snapshot"},
        {"value": 1},
    )
    finalized = _final_proofs(
        (LiveExperimentEvidence(proven, (primitive,), "snapshot-safe-run"),),
        {},
        {},
    )
    assert finalized[0].result is ExperimentResult.BLOCKED
    assert finalized[0].blocked_boundaries == ("remote calculation:E-Pipeline-1",)


def test_ordered_experiment_ids_are_stable() -> None:
    assert tuple(f"E-Pipeline-{number}" for number in range(1, 7)) == EXPERIMENT_IDS


def test_extract_live_minimal_schema_returns_ordered_six_proofs(
    connection: sqlite3.Connection,
) -> None:
    request = LiveProofRequest("safe-run", window().start, window().end)

    evidence = execution.extract_live(connection, request)

    assert tuple(item.proof.experiment_id.value for item in evidence) == EXPERIMENT_IDS
    assert len(evidence) == 6


def test_remote_integrity_counts_all_incidents_but_excludes_withheld_aggregate() -> None:
    clean = RemoteCalculationPrimitive(
        PipelineExperimentId.INTEGRITY,
        window().end,
        1,
        {"metric": "p11.clean", "withheld": "false"},
        {"incident_count": 1},
    )
    withheld = RemoteCalculationPrimitive(
        PipelineExperimentId.INTEGRITY,
        window().end,
        2,
        {"metric": "p11.corrupt", "withheld": "true"},
        {"incident_count": 1},
    )
    events = primitive_events(
        (
            LiveExperimentEvidence(
                proof(PipelineExperimentId.INTEGRITY, ExperimentResult.PROVEN),
                (clean, withheld),
                "p11",
            ),
        ),
        "safe-run",
        window(),
    )

    class Client:
        def query(self, sql: str, parameters: Mapping[str, str | int]) -> list[dict[str, Any]]:
            return [
                {
                    "metric": "p11.clean",
                    "withheld": "false",
                    "incident_rows": 1,
                    "value": 1,
                    "selected_event_ids": [events[0].event_id],
                },
                {
                    "metric": "p11.corrupt",
                    "withheld": "true",
                    "incident_rows": 1,
                    "value": 0,
                    "selected_event_ids": [events[1].event_id],
                },
            ]

    assert remote_calculations(Client(), events, window(), "safe-run") == {
        "E-Pipeline-4": {"p11.clean": 1}
    }


def test_source_lag_float64_transport_accepts_json_numbers_and_rejects_non_numbers() -> None:
    primitive = RemoteCalculationPrimitive(
        PipelineExperimentId.SOURCE_LAG,
        window().end,
        0,
        {"cohort": "cohort-1", "disposition": "accepted"},
        {"lag_seconds": 1.0, "negative_skew": 0},
    )
    events = primitive_events(
        (
            LiveExperimentEvidence(
                proof(PipelineExperimentId.SOURCE_LAG, ExperimentResult.PROVEN),
                (primitive,),
                "pipeline-source-lag-v1",
            ),
        ),
        "safe-run",
        window(),
    )
    row: dict[str, object] = {
        "cohort": "cohort-1",
        "population": 1,
        "accepted": 1,
        "missing_capability": 0,
        "rejected": 0,
        "duplicate": 0,
        "n": 1,
        "p50_lag_seconds": 1,
        "p95_lag_seconds": 1.5,
        "negative_skew_count": 0,
        "invalid_primitive_count": 0,
    }

    class Client:
        def __init__(self, result: Mapping[str, object]) -> None:
            self.result = result

        def query(
            self, sql: str, parameters: Mapping[str, str | int]
        ) -> list[Mapping[str, object]]:
            return [self.result]

    remote = remote_calculations(Client(row), events, window(), "safe-run")
    cohort = remote["E-Pipeline-3"]["cohort-1"]
    assert isinstance(cohort, Mapping)
    assert cohort["p50_lag_seconds"] == 1.0
    assert type(cohort["p50_lag_seconds"]) is float
    assert cohort["p95_lag_seconds"] == 1.5
    assert type(cohort["p95_lag_seconds"]) is float

    for invalid in (True, "1", None):
        malformed = dict(row)
        malformed["p50_lag_seconds"] = invalid
        with pytest.raises(
            PipelineExecutionError, match="remote calculation returned an incorrectly typed scalar"
        ):
            remote_calculations(Client(malformed), events, window(), "safe-run")

    empty = {
        **row,
        "accepted": 0,
        "rejected": 1,
        "n": 0,
        "negative_skew_count": 1,
        "p50_lag_seconds": None,
        "p95_lag_seconds": None,
    }
    empty_remote = remote_calculations(Client(empty), events, window(), "safe-run")
    empty_metrics = empty_remote["E-Pipeline-3"]["cohort-1"]
    assert isinstance(empty_metrics, Mapping)
    assert empty_metrics["n"] == 0
    assert empty_metrics["negative_skew_count"] == 1
    assert "p50_lag_seconds" not in empty_metrics
    assert "p95_lag_seconds" not in empty_metrics
    for percentile in ("p50_lag_seconds", "p95_lag_seconds"):
        with pytest.raises(PipelineExecutionError, match="null percentiles"):
            remote_calculations(Client({**empty, percentile: 0.0}), events, window(), "safe-run")
        missing = {key: value for key, value in empty.items() if key != percentile}
        with pytest.raises(PipelineExecutionError, match="null percentiles"):
            remote_calculations(Client(missing), events, window(), "safe-run")
    with pytest.raises(PipelineExecutionError, match="primitive measurements are invalid"):
        remote_calculations(
            Client({**row, "invalid_primitive_count": 1}), events, window(), "safe-run"
        )


def test_snapshot_direct_queries_bind_multiple_event_ids_as_one_sql_list() -> None:
    primitives = tuple(
        RemoteCalculationPrimitive(
            PipelineExperimentId.SNAPSHOT,
            window().end - timedelta(seconds=1 - index),
            index,
            {
                "event_id": f"source-{index + 1}",
                "scan_digest": f"scan-{index + 1}",
                "bounded_drain_id": f"drain-{index + 1}",
                "terminal_class": "completed",
                "error_class": "none",
            },
            {
                "completed_at_ns": int(window().end.timestamp() * 1_000_000_000) + index,
                "payload_schema_version": 1,
                "duration_ms": 500,
                "rows": 20,
                "logs": 9,
                "traces": 8,
                "context_events": 7,
                "canonical_activities": 6,
                "source_sessions": 5,
                "pending_outbox": 4,
                "failed_during_drain": 3,
            },
        )
        for index in range(2)
    )
    events = primitive_events(
        (
            LiveExperimentEvidence(
                proof(PipelineExperimentId.SNAPSHOT, ExperimentResult.PROVEN),
                primitives,
                "snapshot-query",
            ),
        ),
        "safe-run",
        window(),
    )
    assert events[0].attributes["dashboard.event_id"] == "source-1"
    assert events[0].attributes["dashboard.completed_at_ns"] == str(
        primitives[0].measures["completed_at_ns"]
    )
    calls: list[tuple[str, Mapping[str, str | int]]] = []

    class Client:
        def query(self, sql: str, parameters: Mapping[str, str | int]) -> list[dict[str, object]]:
            calls.append((sql, parameters))
            if "LIMIT 1" in sql:
                return [
                    {
                        "event_id": "source-2",
                        "completed_at_ns": int(window().end.timestamp() * 1_000_000_000),
                        "payload_schema_version": 1,
                        "terminal_class": "completed",
                        "duration_ms": 500,
                        "error_class": "none",
                        "rows": 20,
                        "logs": 9,
                        "traces": 8,
                        "context_events": 7,
                        "canonical_activities": 6,
                        "source_sessions": 5,
                        "pending_outbox": 4,
                        "failed_during_drain": 3,
                        "bounded_drain_id": "drain-2",
                    }
                ]
            if "GROUP BY terminal_class" in sql:
                return [
                    {
                        "terminal_class": "completed",
                        "terminal_scan_count": 2,
                        "terminal_scan_percent": 100.0,
                    }
                ]
            return [
                {
                    "successful_scan_count": 2,
                    "positive_duration_scan_count": 2,
                    "duration_p50_ms": 500.0,
                    "duration_p95_ms": 500.0,
                    "rows_p50": 20.0,
                    "rows_p95": 20.0,
                    "rows_per_second_p50": 40.0,
                    "rows_per_second_p95": 40.0,
                }
            ]

    remote = execution._snapshot_remote(Client(), events, window())
    assert set(remote) == {"a01", "a02", "a05"}
    assert len(calls) == 3
    for sql, parameters in calls:
        assert "IN ({event_0:String}, {event_1:String})" in sql
        assert parameters["event_0"] == events[0].event_id
        assert parameters["event_1"] == events[1].event_id
        assert set(parameters) == {"start", "end", "event_0", "event_1"}
        assert parameters["start"] == window().start.isoformat()
        assert parameters["end"] == window().end.isoformat()


def test_run_composes_all_stages_and_persists_exact_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "live.sqlite3"
    sqlite3.connect(database_path).close()
    output = tmp_path / "evidence.json"
    config = AppConfig(
        database=DatabaseConfig(path=database_path),
        signoz=SigNozConfig(otlp_http_endpoint="http://localhost:4318"),
    )
    calls: list[str] = []
    source = LiveExperimentEvidence(
        proof(PipelineExperimentId.SOURCE_LAG, ExperimentResult.PROVEN),
        (
            RemoteCalculationPrimitive(
                PipelineExperimentId.SOURCE_LAG,
                window().end,
                0,
                {"cohort": "cohort-1", "disposition": "accepted"},
                {"lag_seconds": 1.0, "negative_skew": 0},
            ),
        ),
        "pipeline-source-lag-v1",
        {"cohort-1": {"population": 1}},
    )
    integrity = LiveExperimentEvidence(
        proof(PipelineExperimentId.INTEGRITY, ExperimentResult.PROVEN),
        (
            RemoteCalculationPrimitive(
                PipelineExperimentId.INTEGRITY,
                window().end,
                0,
                {"metric": "p11.clean", "withheld": "false"},
                {"incident_count": 1},
            ),
        ),
        "p11-6b2487cf6f5f395b3409d2de",
    )
    evidence = tuple(
        integrity
        if experiment is PipelineExperimentId.INTEGRITY
        else source
        if experiment is PipelineExperimentId.SOURCE_LAG
        else LiveExperimentEvidence(proof(experiment), (), None)
        for experiment in PipelineExperimentId
    )

    class NativeClient:
        def query(self, sql: str, parameters: Mapping[str, str | int]) -> list[dict[str, object]]:
            assert sql == "SELECT native evidence"
            assert parameters == {"run": "safe-run"}
            return [
                {
                    "primitive_event_count": 4,
                    "distinct_primitive_event_count": 4,
                    "expected_primitive_event_count": 4,
                    "selected_primitive_population_matches": 1,
                    "missing_native_pending_count": 0,
                    "oldest_pending_age_seconds_type": "Nullable(Float64)",
                    "drain_failure_percentage_type": "Nullable(Float64)",
                }
            ]

    monkeypatch.setattr(execution, "validate_output_path", lambda _: output)
    monkeypatch.setattr(execution, "extract_live", lambda *_: calls.append("extract") or evidence)
    monkeypatch.setattr(
        execution,
        "ClickHouseClient",
        lambda **_: calls.append("client") or NativeClient(),
    )

    def record_enqueue(_: sqlite3.Connection, events: list[DerivedEvent]) -> None:
        calls.append(f"enqueue:{events[0].attributes['dashboard.event_kind']}")

    def record_drain(_: sqlite3.Connection, events: list[DerivedEvent], __: str) -> dict[str, int]:
        calls.append(f"drain:{events[0].attributes['dashboard.event_kind']}")
        return {"selected": len(events), "delivered": len(events), "pending": 0}

    def record_ids(_: object, events: list[DerivedEvent]) -> None:
        calls.append(f"ids:{events[0].attributes['dashboard.event_kind']}")

    monkeypatch.setattr(execution, "enqueue_events", record_enqueue)
    monkeypatch.setattr(execution, "_exact_drain", record_drain)
    monkeypatch.setattr(execution, "_verify_ids", record_ids)

    def record_calculations(
        client: execution._RemoteClient, *args: object, **kwargs: object
    ) -> dict[str, Mapping[str, object]]:
        calls.append("calculations")
        assert list(client.query("SELECT native evidence", {"run": "safe-run"}))
        return {
            "E-Pipeline-3": {"cohort-1": {"population": 1}},
            "E-Pipeline-4": {"p11.clean": 1},
        }

    monkeypatch.setattr(execution, "remote_calculations", record_calculations)
    monkeypatch.setattr(
        execution,
        "extract_integrity",
        lambda *_, **__: pytest.fail("live reconciliation must not re-extract SQLite"),
    )

    envelope = run(
        run_id="safe-run",
        window=window(),
        output=output,
        config=config,
    )

    assert calls == [
        "extract",
        "client",
        "enqueue:primitive",
        "drain:primitive",
        "ids:primitive",
        "calculations",
        "enqueue:result",
        "drain:result",
        "ids:result",
    ]
    persisted = output.read_text(encoding="utf-8")
    assert persisted == envelope.canonical_json() + "\n"
    payload = envelope.payload()
    cleanup = payload["cleanup_selector"]
    assert isinstance(cleanup, dict)
    assert cleanup.get("event_ids") == list(
        envelope.primitive_event_ids + envelope.result_event_ids
    )
    source_proof = next(
        proof for proof in envelope.proofs if proof.experiment_id is PipelineExperimentId.SOURCE_LAG
    )
    assert source_proof.result is ExperimentResult.PROVEN
    assert source_proof.assertions["remote_calculation_reconciled"] is True
    local_oracle = payload["domain_local_oracle"]
    remote_result = payload["remote_result"]
    assert isinstance(local_oracle, dict)
    assert isinstance(remote_result, dict)
    assert local_oracle.get("E-Pipeline-3") == {"cohort-1": {"population": 1}}
    assert remote_result.get("E-Pipeline-3") == {"cohort-1": {"population": 1}}
    native_results = payload["native_query_results"]
    assert native_results == [
        {
            "sql": "SELECT native evidence",
            "parameters": {"run": "safe-run"},
            "rows": [
                {
                    "primitive_event_count": 4,
                    "distinct_primitive_event_count": 4,
                    "expected_primitive_event_count": 4,
                    "selected_primitive_population_matches": 1,
                    "missing_native_pending_count": 0,
                    "oldest_pending_age_seconds_type": "Nullable(Float64)",
                    "drain_failure_percentage_type": "Nullable(Float64)",
                }
            ],
        }
    ]
    authority = payload["authority"]
    assert isinstance(authority, dict)
    assert authority["E-Pipeline-3"]["remote_query_id"] == "pipeline-source-lag-v1"
    assert authority["E-Pipeline-1"]["evidence_bundle"] is None


@pytest.mark.parametrize("failure", ["drain", "ids"])
def test_run_stops_before_remote_calculation_on_stage_one_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    database_path = tmp_path / "live.sqlite3"
    sqlite3.connect(database_path).close()
    item = LiveExperimentEvidence(proof(PipelineExperimentId.SNAPSHOT), (), None)
    calls: list[str] = []
    monkeypatch.setattr(execution, "validate_output_path", lambda path: path)
    monkeypatch.setattr(execution, "extract_live", lambda *_: (item,))
    monkeypatch.setattr(execution, "ClickHouseClient", lambda **_: object())
    monkeypatch.setattr(execution, "enqueue_events", lambda *_: calls.append("enqueue"))
    if failure == "drain":
        monkeypatch.setattr(
            execution,
            "_exact_drain",
            lambda *_: (_ for _ in ()).throw(PipelineExecutionError("drain")),
        )
    else:
        monkeypatch.setattr(
            execution,
            "_exact_drain",
            lambda *_: {"selected": 0, "delivered": 0, "pending": 0},
        )
        monkeypatch.setattr(
            execution,
            "_verify_ids",
            lambda *_: (_ for _ in ()).throw(PipelineExecutionError("ids")),
        )
    monkeypatch.setattr(
        execution,
        "remote_calculations",
        lambda *_, **__: calls.append("calculations") or {},
    )

    with pytest.raises(PipelineExecutionError, match=failure):
        run(
            run_id="safe-run",
            window=window(),
            output=tmp_path / "evidence.json",
            config=AppConfig(database=DatabaseConfig(path=database_path)),
        )

    assert calls == ["enqueue"]


@pytest.mark.parametrize(
    "experiment", [PipelineExperimentId.SOURCE_LAG, PipelineExperimentId.INTEGRITY]
)
def test_run_stops_before_results_on_e3_or_e4_reconciliation_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, experiment: PipelineExperimentId
) -> None:
    database_path = tmp_path / "live.sqlite3"
    sqlite3.connect(database_path).close()
    calls: list[str] = []
    if experiment is PipelineExperimentId.SOURCE_LAG:
        local_proof = proof(experiment, ExperimentResult.PROVEN)
        primitive = RemoteCalculationPrimitive(
            experiment,
            window().end,
            0,
            {"cohort": "cohort-1", "disposition": "accepted"},
            {"lag_seconds": 1.0, "negative_skew": 0},
        )
        remote = {"E-Pipeline-3": {"cohort-1": {"population": 2}}}
    else:
        local_proof = proof(experiment, ExperimentResult.PROVEN)
        primitive = RemoteCalculationPrimitive(
            experiment,
            window().end,
            0,
            {"metric": "p11.clean", "withheld": "false"},
            {"incident_count": 1},
        )
        remote = {"E-Pipeline-4": {"p11.clean": 2}}
        monkeypatch.setattr(
            execution,
            "extract_integrity",
            lambda *_, **__: LiveExperimentEvidence(
                PipelineExperimentProof(
                    PipelineExperimentId.INTEGRITY,
                    "safe-run",
                    ExperimentResult.FAILED,
                    EvidenceProvenance.FRESH_REAL,
                    window().identity(),
                    {},
                    {"remote_counts_reconciled": False},
                    (),
                    (),
                    "proposal",
                ),
                (),
                None,
            ),
        )
    evidence = LiveExperimentEvidence(
        local_proof,
        (primitive,),
        "pipeline-source-lag-v1" if experiment is PipelineExperimentId.SOURCE_LAG else "p11",
        {"cohort-1": {"population": 1}} if experiment is PipelineExperimentId.SOURCE_LAG else {},
    )
    evidence_set = tuple(
        evidence if candidate is experiment else LiveExperimentEvidence(proof(candidate), (), None)
        for candidate in PipelineExperimentId
    )
    monkeypatch.setattr(execution, "validate_output_path", lambda path: path)
    monkeypatch.setattr(execution, "extract_live", lambda *_: evidence_set)
    monkeypatch.setattr(execution, "ClickHouseClient", lambda **_: object())
    monkeypatch.setattr(
        execution,
        "enqueue_events",
        lambda _, events: calls.append(f"enqueue:{events[0].attributes['dashboard.event_kind']}"),
    )
    monkeypatch.setattr(
        execution,
        "_exact_drain",
        lambda _, events, __: {"selected": len(events), "delivered": len(events), "pending": 0},
    )
    monkeypatch.setattr(execution, "_verify_ids", lambda *_: None)
    monkeypatch.setattr(execution, "remote_calculations", lambda *_, **__: remote)

    with pytest.raises(PipelineExecutionError, match="remote calculation reconciliation mismatch"):
        run(
            run_id="safe-run",
            window=window(),
            output=tmp_path / "evidence.json",
            config=AppConfig(database=DatabaseConfig(path=database_path)),
        )

    assert calls == ["enqueue:primitive"]
    assert not (tmp_path / "evidence.json").exists()


@pytest.mark.parametrize("experiment", list(PipelineExperimentId))
def test_missing_authority_downgrades_every_pipeline_proof(
    experiment: PipelineExperimentId,
) -> None:
    proven = PipelineExperimentProof(
        experiment,
        "safe-run",
        ExperimentResult.PROVEN,
        EvidenceProvenance.FRESH_REAL,
        window().identity(),
        {"population": 1},
        {"bounded": True},
        ("immutable-source-id",),
        (),
        "proposal",
    )
    primitive = RemoteCalculationPrimitive(
        experiment,
        window().end,
        0,
        {"cohort": "cohort-1"},
        {"value": 1},
    )
    item = LiveExperimentEvidence(proven, (primitive,), "direct-query-v1")

    finalized = _final_proofs((item,), {}, {})

    assert finalized[0].result is ExperimentResult.BLOCKED
    with pytest.raises(ValueError, match="null evidence bundle"):
        execution.PipelineExecutionAuthority(
            finalized[0],
            ("immutable-delivered-id",),
            "result-id",
            "direct-query-v1",
            {"value": 1},
            {"value": 1},
            {
                "primitive_selected": 1,
                "primitive_delivered": 1,
                "result_selected": 1,
                "result_delivered": 1,
            },
        )


@pytest.mark.parametrize(
    "experiment",
    [
        PipelineExperimentId.SNAPSHOT,
        PipelineExperimentId.TERMINAL_CADENCE,
        PipelineExperimentId.OUTBOX,
        PipelineExperimentId.LEDGER,
    ],
)
def test_remote_oracle_mismatch_fails_every_direct_pipeline_path(
    experiment: PipelineExperimentId,
) -> None:
    proven = PipelineExperimentProof(
        experiment,
        "safe-run",
        ExperimentResult.PROVEN,
        EvidenceProvenance.FRESH_REAL,
        window().identity(),
        {"population": 1},
        {"bounded": True},
        ("immutable-source-id",),
        (),
        "proposal",
    )
    item = LiveExperimentEvidence(
        proven,
        (
            RemoteCalculationPrimitive(
                experiment, window().end, 0, {"cohort": "cohort-1"}, {"value": 1}
            ),
        ),
        "direct-query-v1",
    )
    finalized = _final_proofs(
        (item,),
        {experiment.value: {"value": 2}},
        {experiment.value: {"value": 1}},
    )

    assert finalized[0].result is ExperimentResult.FAILED
    assert finalized[0].assertions["remote_calculation_reconciled"] is False


def test_e5_direct_query_binds_exact_immutable_population() -> None:
    end = window().end
    item = LiveExperimentEvidence(
        proof(PipelineExperimentId.OUTBOX),
        cast(
            tuple[RemoteCalculationPrimitive, ...],
            (
                E5OutboxEventPrimitive(
                    PipelineExperimentId.OUTBOX,
                    "event-0",
                    end - timedelta(seconds=2),
                    "otlp",
                    "derived_event",
                    OutboxEventStatus.DELIVERED,
                ),
                E5OutboxEventPrimitive(
                    PipelineExperimentId.OUTBOX,
                    "event-1",
                    end - timedelta(seconds=1),
                    "otlp",
                    "derived_event",
                    OutboxEventStatus.PENDING,
                ),
                E5DeliveryAttemptPrimitive(
                    PipelineExperimentId.OUTBOX,
                    "attempt-1",
                    "event-1",
                    "drain-1",
                    end - timedelta(seconds=1),
                    DeliveryAttemptStatus.FAILED,
                    "TimeoutError",
                ),
                E5FinalDrainPrimitive(PipelineExperimentId.OUTBOX, "drain-1", end),
            ),
        ),
        "pipeline-outbox-current-pending-v1",
    )
    events = primitive_events((item,), "safe-run", window())
    calls: list[tuple[str, Mapping[str, str | int]]] = []

    class Client:
        def query(self, sql: str, parameters: Mapping[str, str | int]) -> list[dict[str, Any]]:
            calls.append((sql, parameters))
            return [
                {
                    "selected_event_count": 2,
                    "pending_event_count": 1,
                    "attempted_event_count": 1,
                    "failed_event_count": 1,
                    "final_drain_attempt_count": 1,
                    "oldest_pending_age_seconds": 1.0,
                    "drain_failure_percentage": 100.0,
                    "oldest_pending_age_seconds_type": "Nullable(Float64)",
                    "drain_failure_percentage_type": "Nullable(Float64)",
                    "final_drain_completion_count": 1,
                    "final_drain_completed_at_ns": int(end.timestamp() * 1_000_000_000),
                    "matching_final_drain_completion_count": 1,
                    "primitive_event_count": 4,
                    "distinct_primitive_event_count": 4,
                    "expected_primitive_event_count": 4,
                    "selected_primitive_population_matches": 1,
                    "missing_native_pending_count": 0,
                }
            ]

    assert execution._outbox_remote(Client(), item, events, window()) == {
        "selected_event_count": 2,
        "pending_event_count": 1,
        "attempted_event_count": 1,
        "failed_event_count": 1,
        "final_drain_attempt_count": 1,
        "oldest_pending_age_seconds": 1.0,
        "drain_failure_percentage": 100.0,
    }
    bindings = calls[0][1]
    assert bindings["event_ids"] == '["event-0", "event-1"]'
    assert bindings["final_drain_id"] == "drain-1"
    assert bindings["expected_final_drain_completed_at_ns"] == int(end.timestamp() * 1_000_000_000)
    assert all(
        isinstance(bindings[field], int)
        for field in (
            "source_start_ns",
            "source_end_ns",
            "expected_final_drain_completed_at_ns",
        )
    )

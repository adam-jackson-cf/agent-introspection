from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from experiments.dashboard_prototype import request_execution as execution
from experiments.dashboard_prototype.contracts import EvidenceProvenance, ExperimentResult
from experiments.dashboard_prototype.experiment_live_common import (
    LiveProofRequest,
    RemoteCalculationPrimitive,
)
from experiments.dashboard_prototype.request_common import (
    RequestExperimentId,
    RequestExperimentProof,
    RequestLiveEvidence,
)


def window() -> execution.ExtractionWindow:
    end = datetime(2026, 9, 2, tzinfo=UTC)
    return execution.ExtractionWindow(end - timedelta(minutes=5), end)


def audit() -> RequestLiveEvidence:
    proof = RequestExperimentProof(
        RequestExperimentId.FIELD_AUDIT,
        "safe-run",
        ExperimentResult.BLOCKED,
        EvidenceProvenance.FRESH_REAL,
        window().identity(),
        {},
        {"bounded": True},
        (),
        ("authority",),
        "proposal",
    )
    primitive = RemoteCalculationPrimitive(
        RequestExperimentId.FIELD_AUDIT,
        window().end,
        1,
        {
            "producer": "omp",
            "surface": "omp",
            "field": "logical_request_id",
            "authority_state": "absent",
        },
        {"classification_count": 1},
    )
    return RequestLiveEvidence(proof, (primitive,), None, {})


def test_primitives_are_e1_only_and_scalar_safe() -> None:
    events = execution.primitive_events((audit(),), "safe-run", window())
    assert len(events) == 1
    assert events[0].attributes["dashboard.authority_state"] == "absent"
    assert events[0].attributes["dashboard.classification_count"] == 1
    with pytest.raises(execution.RequestExecutionError):
        execution._safe_attributes(audit(), {"prompt": "no"}, {"classification_count": 1})


def test_privacy_allows_only_structural_classification_cohorts() -> None:
    native = execution._classification_cohort("omp", "omp", "native_session_id", "authoritative")
    changed_surface = execution._classification_cohort(
        "omp", "codex-cli", "native_session_id", "authoritative"
    )
    changed_state = execution._classification_cohort("omp", "omp", "native_session_id", "absent")
    assert native == "omp.omp.native_session_id.authoritative"
    assert native != changed_surface
    assert native != changed_state
    execution._reject_unsafe({native: 1})
    with pytest.raises(execution.RequestExecutionError):
        execution._reject_unsafe({"omp.omp.native_session_id.authoritative": True})
    with pytest.raises(execution.RequestExecutionError):
        execution._reject_unsafe({"omp.codex-cli.native_session_id.authoritative": 1})
    with pytest.raises(execution.RequestExecutionError):
        execution._reject_unsafe({"omp.omp.native_session_payload.authoritative": 1})
    with pytest.raises(execution.RequestExecutionError):
        execution._reject_unsafe({"response_payload": 1})


def test_remote_exact_calculation_and_mismatch_abort() -> None:
    events = execution.primitive_events((audit(),), "safe-run", window())

    class Client:
        def query(self, sql: str, parameters: object) -> list[dict[str, object]]:
            assert isinstance(parameters, dict)
            assert parameters["query_id"] == "request-field-audit-v1"
            return [
                {
                    "producer": "omp",
                    "surface": "omp",
                    "field": "logical_request_id",
                    "state": "absent",
                    "count": 1,
                }
            ]

    remote = execution.remote_calculations(Client(), events, window(), "safe-run")
    request = LiveProofRequest("safe-run", window().start, window().end)
    proofs = execution._final_proofs(
        (
            audit(),
            execution._request_attempt_blocked(audit().proof, request),
            execution._remote_calculation_blocked(audit().proof, request),
        ),
        remote,
        events,
    )
    _, parameters = execution._params(events, window(), "safe-run")
    assert parameters["start_bucket"] == max(0, int(window().start.timestamp()) - 1800)
    assert parameters["end_bucket"] == int(window().end.timestamp())
    assert parameters["query_id"] == "request-field-audit-v1"
    assert proofs[0].experiment_id is RequestExperimentId.FIELD_AUDIT
    assert proofs[0].assertions["remote_calculation_reconciled"] is True
    expected_binding = proofs[0].content_hash()[:16]
    for dependent in proofs[1:]:
        assert dependent.metrics["audit_proof_bound"] == expected_binding
        assert dependent.evidence_ids == (f"request-audit:{expected_binding}",)
    envelope = execution.RunEnvelope(
        execution.NAMESPACE,
        "safe-run",
        window(),
        proofs,
        tuple(event.event_id for event in events),
        (),
        execution._local_oracle(proofs, events),
        remote,
        {},
    )
    payload = envelope.payload()
    for proof_payload in payload["proofs"][1:]:
        assert proof_payload["metrics"]["audit_proof_bound"] == expected_binding
        assert proof_payload["evidence_ids"] == [f"request-audit:{expected_binding}"]
    assert payload["domain_local_oracle"]["E-Request-2"]["audit_proof_bound"] == expected_binding
    assert payload["domain_local_oracle"]["E-Request-3"]["audit_proof_bound"] == expected_binding

    class InvalidClient:
        def query(self, sql: str, parameters: object) -> list[dict[str, object]]:
            return [
                {
                    "producer": "omp",
                    "surface": "omp",
                    "field": "logical_request_id",
                    "state": "absent",
                    "count": "one",
                }
            ]

    with pytest.raises(execution.RequestExecutionError):
        execution.remote_calculations(InvalidClient(), events, window(), "safe-run")
    with pytest.raises(execution.RequestExecutionError):
        execution._final_proofs((audit(),), {"E-Request-1": {}}, events)


def test_results_envelope_and_exact_id_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    request = LiveProofRequest("safe-run", window().start, window().end)
    attempts = execution._request_attempt_blocked(audit().proof, request)
    remote = execution._remote_calculation_blocked(audit().proof, request)
    assert attempts.proof.blocked_boundaries == (
        "authoritative-logical-request-identity",
        "authoritative-attempt-identity",
        "authoritative-accounting-identity",
    )
    assert attempts.primitives == ()
    proofs = (audit().proof, attempts.proof, remote.proof)
    results = execution.result_events(proofs, "safe-run", window())
    seen = iter(([], [item.event_id for item in results]))
    delays: list[float] = []
    monkeypatch.setattr(execution, "remote_event_ids", lambda *_: next(seen))
    monkeypatch.setattr(execution, "sleep", delays.append)
    execution._verify_ids(object(), results)
    assert delays == [0.5]
    envelope = execution.RunEnvelope(
        execution.NAMESPACE,
        "safe-run",
        window(),
        proofs,
        (),
        tuple(event.event_id for event in results),
        {},
        {},
        {},
    )
    assert envelope.canonical_json() == envelope.canonical_json()
    assert envelope.payload()["cleanup_selector"]["event_ids"] == [
        event.event_id for event in results
    ]

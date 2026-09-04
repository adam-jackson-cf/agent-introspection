from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from experiments.dashboard_prototype.contracts import (
    EvidenceProvenance,
    ExperimentResult,
    PrototypeContractError,
)
from experiments.dashboard_prototype.recurrence_common import (
    SUPPORTED_SURFACES,
    RecurrenceExperimentId,
    RecurrenceExperimentProof,
    RecurrenceLiveEvidence,
    RecurrencePrimitive,
)

START = datetime(2026, 9, 2, 12, tzinfo=UTC)
END = START + timedelta(minutes=5)


def _time_ns(value: datetime) -> int:
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


def _proof(
    experiment_id: RecurrenceExperimentId = RecurrenceExperimentId.WAVE_A,
) -> RecurrenceExperimentProof:
    return RecurrenceExperimentProof(
        experiment_id=experiment_id,
        run_id="recurrence-run-1",
        result=ExperimentResult.PROVEN,
        provenance=EvidenceProvenance.FRESH_REAL,
        source_start=START,
        source_end=END,
        source_boundary="bounded-live-source",
        metrics={"finding_count": 1, "confidence": 1.0},
        assertions={"remote_calculation_reconciled": True, "population_complete": True},
        evidence_ids=("remote-event-1",),
        blocked_boundaries=(),
        proposal="retain-recurrence-projection",
    )


def _primitive(
    experiment_id: RecurrenceExperimentId = RecurrenceExperimentId.WAVE_A,
) -> RecurrencePrimitive:
    return RecurrencePrimitive(
        experiment_id=experiment_id,
        source_time=END,
        ordinal=0,
        dimensions={
            "producer": "codex-app-server",
            "surface": "codex-app-server",
            "stable_identity": "activity-fingerprint",
            "policy_identity": "measure-policy",
        },
        measures={"reducer_counts": 1.0},
    )


@pytest.mark.parametrize("experiment_id", RecurrenceExperimentId)
def test_recurrence_experiment_ids_are_constructible(experiment_id: RecurrenceExperimentId) -> None:
    proof = _proof(experiment_id)

    assert proof.experiment_id is experiment_id


def test_supported_surfaces_are_exactly_canonical_pairs() -> None:
    assert SUPPORTED_SURFACES == (
        ("omp", "omp"),
        ("codex-cli", "codex-cli"),
        ("codex-app-server", "codex-app-server"),
    )


def test_recurrence_proof_has_deterministic_immutable_content_hash() -> None:
    proof = _proof()
    reordered = replace(
        proof,
        metrics={"confidence": 1.0, "finding_count": 1},
        assertions={"population_complete": True, "remote_calculation_reconciled": True},
    )

    assert proof.canonical_json() == reordered.canonical_json()
    assert proof.content_hash() == reordered.content_hash()
    with pytest.raises(TypeError):
        cast(dict[str, int], proof.metrics)["finding_count"] = 2


def test_live_evidence_retains_distinct_immutable_source_ids() -> None:
    evidence = RecurrenceLiveEvidence(
        _proof(RecurrenceExperimentId.FINDING_PROJECTION),
        (_primitive(RecurrenceExperimentId.FINDING_PROJECTION),),
        "remote-calculation-1",
        finding_version_source_ids=("finding-version-immutable-1",),
        canonical_task_membership_source_ids=("canonical-membership-immutable-1",),
    )

    assert evidence.finding_version_source_ids == ("finding-version-immutable-1",)
    assert evidence.canonical_task_membership_source_ids == ("canonical-membership-immutable-1",)
    with pytest.raises(PrototypeContractError, match="finding-version source IDs"):
        replace(evidence, finding_version_source_ids=("duplicate-1", "duplicate-1"))


@pytest.mark.parametrize(
    "provenance",
    [EvidenceProvenance.SYNTHETIC, EvidenceProvenance.RETAINED],
)
def test_proven_requires_fresh_real_evidence_and_reconciliation(
    provenance: EvidenceProvenance,
) -> None:
    with pytest.raises(PrototypeContractError, match="fresh-real"):
        replace(_proof(), provenance=provenance)
    with pytest.raises(PrototypeContractError, match="exact evidence IDs"):
        replace(_proof(), evidence_ids=())
    with pytest.raises(PrototypeContractError, match="remote_calculation_reconciled"):
        replace(_proof(), assertions={"remote_calculation_reconciled": False})


def test_blocked_and_failed_proofs_name_their_contract_condition() -> None:
    with pytest.raises(PrototypeContractError, match="missing_durable_authority"):
        replace(
            _proof(),
            result=ExperimentResult.BLOCKED,
            provenance=EvidenceProvenance.RETAINED,
            evidence_ids=(),
            blocked_boundaries=("remote-query-unavailable",),
        )
    blocked_with_retained_evidence = replace(
        _proof(),
        result=ExperimentResult.BLOCKED,
        provenance=EvidenceProvenance.RETAINED,
        evidence_ids=("remote-event-1",),
        blocked_boundaries=("missing_durable_authority",),
    )
    blocked = replace(
        _proof(),
        result=ExperimentResult.BLOCKED,
        provenance=EvidenceProvenance.RETAINED,
        evidence_ids=(),
        blocked_boundaries=("missing_durable_authority",),
    )
    with pytest.raises(PrototypeContractError, match="contradiction_detected"):
        replace(
            _proof(),
            result=ExperimentResult.FAILED,
            assertions={"population_complete": False},
        )
    failed = replace(
        _proof(),
        result=ExperimentResult.FAILED,
        assertions={"contradiction_detected": True},
    )

    assert blocked_with_retained_evidence.evidence_ids == ("remote-event-1",)
    assert blocked.result is ExperimentResult.BLOCKED
    assert failed.result is ExperimentResult.FAILED


@pytest.mark.parametrize("unsafe_value", [float("nan"), float("inf"), float("-inf")])
def test_proof_rejects_nonfinite_metrics(unsafe_value: float) -> None:
    with pytest.raises(PrototypeContractError, match="finite"):
        replace(_proof(), metrics={"finding_count": unsafe_value})


def test_proof_rejects_duplicate_or_invalid_evidence_ids() -> None:
    with pytest.raises(PrototypeContractError, match="unique"):
        replace(_proof(), evidence_ids=("remote-event-1", "remote-event-1"))
    with pytest.raises(PrototypeContractError, match="allowlisted token"):
        replace(_proof(), evidence_ids=("unsafe/evidence",))


@pytest.mark.parametrize(
    "field_name",
    ["prompt", "command_output", "payload", "session_id", "file_path"],
)
def test_primitive_rejects_privacy_boundary_fields(field_name: str) -> None:
    with pytest.raises(ValueError, match="privacy boundary"):
        RecurrencePrimitive(
            experiment_id=RecurrenceExperimentId.WAVE_A,
            source_time=END,
            ordinal=0,
            dimensions={field_name: "safe-value"},
            measures={},
        )


def test_primitive_enforces_frozen_privacy_key_allowlist_and_finite_scalars() -> None:
    primitive = _primitive()

    assert primitive.dimensions["producer"] == "codex-app-server"
    with pytest.raises(ValueError, match="frozen-allowlisted"):
        replace(primitive, dimensions={"custom_field": "safe-value"}, measures={})
    with pytest.raises(ValueError, match="frozen-allowlisted"):
        replace(primitive, dimensions={}, measures={"finding_count": 1.0})
    with pytest.raises(ValueError, match="finite"):
        replace(primitive, measures={"reducer_counts": float("nan")})
    with pytest.raises(ValueError, match="numeric"):
        replace(primitive, measures={"reducer_counts": cast(float, True)})


@pytest.mark.parametrize("invalid_ns", [True, -1, 2**63])
def test_primitive_rejects_invalid_source_time_ns(invalid_ns: int) -> None:
    with pytest.raises(ValueError, match="signed Int64"):
        replace(_primitive(), source_time_ns=invalid_ns)


def test_live_evidence_uses_exact_ns_when_available() -> None:
    primitive = _primitive()
    start_ns = _time_ns(START)
    end_ns = _time_ns(END)

    assert RecurrenceLiveEvidence(
        _proof(),
        [replace(primitive, source_time=START, source_time_ns=start_ns + 1)],
        "remote-query-1",
    ).primitives == (replace(primitive, source_time=START, source_time_ns=start_ns + 1),)
    assert RecurrenceLiveEvidence(
        _proof(), [replace(primitive, source_time=START, source_time_ns=end_ns)], "remote-query-1"
    ).primitives == (replace(primitive, source_time=START, source_time_ns=end_ns),)
    with pytest.raises(ValueError, match=">start <=end"):
        RecurrenceLiveEvidence(
            _proof(),
            [replace(primitive, source_time=END, source_time_ns=start_ns)],
            "remote-query-1",
        )


def test_live_evidence_freezes_tuple_and_enforces_datetime_source_membership() -> None:
    primitive = _primitive()
    evidence = RecurrenceLiveEvidence(_proof(), [primitive], "remote-query-1")

    assert evidence.primitives == (primitive,)
    with pytest.raises(ValueError, match=">start <=end"):
        RecurrenceLiveEvidence(
            _proof(),
            [replace(primitive, source_time=START)],
            "remote-query-1",
        )
    with pytest.raises(ValueError, match="remote calculation"):
        RecurrenceLiveEvidence(_proof(), (), None)

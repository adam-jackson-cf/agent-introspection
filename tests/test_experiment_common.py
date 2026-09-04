from dataclasses import replace
from enum import StrEnum
from typing import cast

import pytest

from experiments.dashboard_prototype.contracts import (
    EvidenceProvenance,
    ExperimentResult,
    PrototypeContractError,
)
from experiments.dashboard_prototype.experiment_common import ExperimentProof


class _ExperimentId(StrEnum):
    SAMPLE = "E-Sample-1"


def _proof() -> ExperimentProof[_ExperimentId]:
    return ExperimentProof[_ExperimentId](
        experiment_id=_ExperimentId.SAMPLE,
        run_id="run-20260901-sample-1",
        result=ExperimentResult.PROVEN,
        provenance=EvidenceProvenance.FRESH_REAL,
        source_boundary="completed sample boundary",
        metrics={"sample_count": 1},
        assertions={"sample_reconciled": True},
        evidence_ids=("sample-1",),
        blocked_boundaries=(),
        proposal="Project one immutable sample event.",
    )


def test_experiment_proof_is_deterministic_immutable_and_canonical() -> None:
    original = _proof()
    reordered = replace(
        original,
        metrics={"sample_count": 1},
        assertions={"sample_reconciled": True},
    )

    expected = (
        '{"assertions":{"sample_reconciled":true},"blocked_boundaries":[],'
        '"evidence_ids":["sample-1"],"experiment_id":"E-Sample-1",'
        '"experiment_namespace":"agent-introspection.dashboard-prototype.v1",'
        '"metrics":{"sample_count":1},"proposal":"Project one immutable sample event.",'
        '"provenance":"fresh-real","result":"Proven",'
        '"run_id":"run-20260901-sample-1",'
        '"source_boundary":"completed sample boundary"}'
    )
    assert original.content_hash() == (
        "2e2d9b8d39e90d8fe927a77d5aa371e0833ac5aac6c177187f21b79203cc1373"
    )
    assert original.canonical_json() == expected
    assert original.canonical_json() == reordered.canonical_json()
    assert original.content_hash() == reordered.content_hash()
    with pytest.raises(TypeError):
        cast(dict[str, int], original.metrics)["sample_count"] = 2


def test_proven_experiment_proof_requires_fresh_real_passing_evidence() -> None:
    with pytest.raises(PrototypeContractError, match="fresh-real"):
        replace(_proof(), provenance=EvidenceProvenance.RETAINED)
    with pytest.raises(PrototypeContractError, match="every assertion"):
        replace(_proof(), assertions={"sample_reconciled": False})
    with pytest.raises(PrototypeContractError, match="every assertion"):
        replace(_proof(), blocked_boundaries=("missing remote query",))


def test_blocked_experiment_proof_requires_exact_missing_boundary() -> None:
    with pytest.raises(PrototypeContractError, match="missing boundary"):
        replace(
            _proof(),
            result=ExperimentResult.BLOCKED,
            provenance=EvidenceProvenance.RETAINED,
        )
    blocked = replace(
        _proof(),
        result=ExperimentResult.BLOCKED,
        provenance=EvidenceProvenance.RETAINED,
        assertions={"remote_query_executed": False},
        blocked_boundaries=("bounded remote query not executed",),
    )

    assert blocked.result is ExperimentResult.BLOCKED

import json

import pytest

from experiments.dashboard_prototype.contracts import EvidenceProvenance, ExperimentResult
from experiments.dashboard_prototype.pipeline_common import (
    PipelineExecutionAuthority,
    PipelineExperimentId,
    PipelineExperimentProof,
)


@pytest.mark.parametrize(
    ("experiment_id", "expected_id"),
    [
        (PipelineExperimentId.SNAPSHOT, "E-Pipeline-1"),
        (PipelineExperimentId.TERMINAL_CADENCE, "E-Pipeline-2"),
        (PipelineExperimentId.SOURCE_LAG, "E-Pipeline-3"),
        (PipelineExperimentId.INTEGRITY, "E-Pipeline-4"),
        (PipelineExperimentId.OUTBOX, "E-Pipeline-5"),
        (PipelineExperimentId.LEDGER, "E-Pipeline-6"),
    ],
)
def test_pipeline_proof_serializes_registered_experiment_id(
    experiment_id: PipelineExperimentId, expected_id: str
) -> None:
    proof = PipelineExperimentProof(
        experiment_id=experiment_id,
        run_id="run-20260901-pipeline-1",
        result=ExperimentResult.PROVEN,
        provenance=EvidenceProvenance.FRESH_REAL,
        source_boundary="completed pipeline boundary",
        metrics={"scan_count": 1},
        assertions={"durable_counts_reconciled": True},
        evidence_ids=("scan-1",),
        blocked_boundaries=(),
        proposal="Project the reconciled snapshot as one immutable event.",
    )

    assert json.loads(proof.canonical_json())["experiment_id"] == expected_id


def test_blocked_execution_authority_retains_only_exact_blockers() -> None:
    proof = PipelineExperimentProof(
        experiment_id=PipelineExperimentId.OUTBOX,
        run_id="run-20260901-pipeline-1",
        result=ExperimentResult.BLOCKED,
        provenance=EvidenceProvenance.FRESH_REAL,
        source_boundary="completed pipeline boundary",
        metrics={"pending_count": 0},
        assertions={"final_drain_authoritative": False},
        evidence_ids=(),
        blocked_boundaries=("application-owned final drain",),
        proposal="Wait for the immutable final-drain authority.",
    )

    authority = PipelineExecutionAuthority(
        proof=proof,
        primitive_event_ids=(),
        result_event_id="result-event-1",
        remote_query_id=None,
        local_oracle={},
        remote_result={},
        delivery={
            "primitive_selected": 0,
            "primitive_delivered": 0,
            "result_selected": 1,
            "result_delivered": 1,
        },
    )

    assert authority.payload() == {
        "blocked_boundaries": ["application-owned final drain"],
        "evidence_bundle": None,
    }


def test_proven_execution_authority_requires_proof_evidence_ids() -> None:
    proof = PipelineExperimentProof(
        experiment_id=PipelineExperimentId.SOURCE_LAG,
        run_id="run-20260901-pipeline-1",
        result=ExperimentResult.PROVEN,
        provenance=EvidenceProvenance.FRESH_REAL,
        source_boundary="completed pipeline boundary",
        metrics={"population": 1},
        assertions={"remote_calculation_reconciled": True},
        evidence_ids=(),
        blocked_boundaries=(),
        proposal="Project only authoritative source-lag rows.",
    )

    with pytest.raises(ValueError, match="complete matching authority"):
        PipelineExecutionAuthority(
            proof=proof,
            primitive_event_ids=("primitive-event-1",),
            result_event_id="result-event-1",
            remote_query_id="pipeline-source-lag-v1",
            local_oracle={"cohort-1": {"population": 1}},
            remote_result={"cohort-1": {"population": 1}},
            delivery={
                "primitive_selected": 1,
                "primitive_delivered": 1,
                "result_selected": 1,
                "result_delivered": 1,
            },
        )

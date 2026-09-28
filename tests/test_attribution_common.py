import json

import pytest

from experiments.dashboard_prototype.attribution_common import (
    AttributionExperimentId,
    AttributionExperimentProof,
    AttributionRowEvidence,
    AttributionRowState,
)
from experiments.dashboard_prototype.contracts import EvidenceProvenance, ExperimentResult


@pytest.mark.parametrize(
    ("experiment_id", "expected_id"),
    [
        (AttributionExperimentId.BASELINE, "E-Attribution-1"),
        (AttributionExperimentId.CODEX_APP_SERVER, "E-Attribution-2"),
        (AttributionExperimentId.CLAUDE_BOUNDARY, "E-Attribution-3"),
        (AttributionExperimentId.LIFECYCLE_DELAY, "E-Attribution-4"),
        (AttributionExperimentId.LATE_CONTEXT, "E-Attribution-5"),
    ],
)
def test_attribution_proof_serializes_registered_experiment_id(
    experiment_id: AttributionExperimentId, expected_id: str
) -> None:
    proof = AttributionExperimentProof(
        experiment_id=experiment_id,
        run_id="run-20260901-attribution-1",
        result=ExperimentResult.PROVEN,
        provenance=EvidenceProvenance.FRESH_REAL,
        source_boundary="completed attribution boundary",
        metrics={"attributed_sessions": 1},
        assertions={"identity_joined": True},
        evidence_ids=("attribution-1",),
        blocked_boundaries=(),
        proposal="Project exact native identity attribution.",
    )

    assert json.loads(proof.canonical_json())["experiment_id"] == expected_id


def test_blocked_row_cannot_retain_aggregate_evidence() -> None:
    with pytest.raises(ValueError, match="blocked rows retain no evidence bundle"):
        AttributionRowEvidence(
            experiment_id=AttributionExperimentId.BASELINE,
            row_id="A07",
            producer="omp",
            state=AttributionRowState.BLOCKED,
            blocked_reason="no_authoritative_context",
            members=(),
            direct_remote_sql_parameters=None,
            direct_remote_sql_parameter_types=None,
            direct_remote_result=None,
            direct_remote_result_types=None,
            oracle_parameters=None,
            oracle_parameter_types=None,
            oracle_result=None,
            oracle_result_types=None,
        )


def test_blocked_row_cannot_retain_native_lineage() -> None:
    with pytest.raises(ValueError, match="blocked rows retain no evidence bundle"):
        AttributionRowEvidence(
            experiment_id=AttributionExperimentId.CODEX_APP_SERVER,
            row_id="A07",
            producer="codex-app-server",
            state=AttributionRowState.BLOCKED,
            blocked_reason="no_row_authority",
            members=(),
            direct_remote_sql_parameters=None,
            direct_remote_sql_parameter_types=None,
            direct_remote_result=None,
            direct_remote_result_types=None,
            oracle_parameters=None,
            oracle_parameter_types=None,
            oracle_result=None,
            oracle_result_types=None,
        )

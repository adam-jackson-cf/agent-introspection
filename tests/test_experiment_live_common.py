from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from experiments.dashboard_prototype.attribution_common import (
    AttributionExperimentId,
    AttributionExperimentProof,
)
from experiments.dashboard_prototype.attribution_live_common import (
    AttributionCalculationPrimitive,
    AttributionLiveEvidence,
)
from experiments.dashboard_prototype.contracts import EvidenceProvenance, ExperimentResult
from experiments.dashboard_prototype.experiment_live_common import (
    LiveExperimentEvidence,
    LiveProofRequest,
    RemoteCalculationPrimitive,
)
from experiments.dashboard_prototype.pipeline_live_common import (
    LiveExperimentEvidence as PipelineLiveEvidence,
)
from experiments.dashboard_prototype.pipeline_live_common import (
    RemoteCalculationPrimitive as PipelineCalculationPrimitive,
)

NOW = datetime(2026, 9, 1, tzinfo=UTC)


def _proof(experiment_id: AttributionExperimentId) -> AttributionExperimentProof:
    return AttributionExperimentProof(
        experiment_id=experiment_id,
        run_id="attribution-live-run",
        result=ExperimentResult.PROVEN,
        provenance=EvidenceProvenance.FRESH_REAL,
        source_boundary="bounded-source",
        metrics={"population": 1},
        assertions={"bounded": True},
        evidence_ids=("evidence-1",),
        blocked_boundaries=(),
        proposal="retain exact attribution identity",
    )


@pytest.mark.parametrize("experiment_id", AttributionExperimentId)
def test_attribution_specializations_construct_proven_evidence(
    experiment_id: AttributionExperimentId,
) -> None:
    primitive = AttributionCalculationPrimitive(
        experiment_id=experiment_id,
        source_time=NOW,
        ordinal=0,
        dimensions={"producer": "codex-app-server"},
        measures={"count": 1},
    )

    evidence = AttributionLiveEvidence(_proof(experiment_id), (primitive,), "attribution-query")

    assert evidence.primitives == (primitive,)


def test_live_contract_has_one_generic_runtime_implementation() -> None:
    assert PipelineCalculationPrimitive.__post_init__ is RemoteCalculationPrimitive.__post_init__
    assert AttributionCalculationPrimitive.__post_init__ is RemoteCalculationPrimitive.__post_init__
    assert PipelineLiveEvidence.__post_init__ is LiveExperimentEvidence.__post_init__
    assert AttributionLiveEvidence.__post_init__ is LiveExperimentEvidence.__post_init__
    assert LiveProofRequest("safe-run", NOW, NOW + timedelta(seconds=1)).source_boundary.endswith(
        "+00:00"
    )


@pytest.mark.parametrize(
    "field_name",
    [
        "prompt",
        "response",
        "transcript",
        "command",
        "secret",
        "credential",
        "path",
        "native_session",
        "claude",
    ],
)
def test_generic_primitive_rejects_each_prohibited_privacy_field(field_name: str) -> None:
    with pytest.raises(ValueError, match="field name is not allowlisted"):
        RemoteCalculationPrimitive(
            experiment_id=AttributionExperimentId.BASELINE,
            source_time=NOW,
            ordinal=0,
            dimensions={field_name: "safe-value"},
            measures={},
        )


def test_generic_evidence_rejects_mismatched_primitive_identity() -> None:
    primitive = RemoteCalculationPrimitive(
        experiment_id=AttributionExperimentId.LATE_CONTEXT,
        source_time=NOW,
        ordinal=0,
        dimensions={"producer": "codex-app-server"},
        measures={"count": 1},
    )

    with pytest.raises(ValueError, match="experiment identity"):
        LiveExperimentEvidence(_proof(AttributionExperimentId.BASELINE), (primitive,), "query")

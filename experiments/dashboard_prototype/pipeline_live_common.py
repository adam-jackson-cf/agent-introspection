"""Pipeline specializations of the generic live evidence contract."""

from __future__ import annotations

from experiments.dashboard_prototype.experiment_live_common import (
    LiveExperimentEvidence as GenericLiveExperimentEvidence,
)
from experiments.dashboard_prototype.experiment_live_common import LiveProofRequest
from experiments.dashboard_prototype.experiment_live_common import (
    RemoteCalculationPrimitive as GenericRemoteCalculationPrimitive,
)
from experiments.dashboard_prototype.pipeline_common import (
    PipelineExperimentId,
    PipelineExperimentProof,
)

__all__ = ("LiveExperimentEvidence", "LiveProofRequest", "RemoteCalculationPrimitive")


class RemoteCalculationPrimitive(GenericRemoteCalculationPrimitive[PipelineExperimentId]):
    """Pipeline scalar input row."""


class LiveExperimentEvidence(GenericLiveExperimentEvidence[PipelineExperimentId]):
    """Pipeline local proof and remote-calculation evidence."""

    proof: PipelineExperimentProof
    primitives: tuple[RemoteCalculationPrimitive, ...]

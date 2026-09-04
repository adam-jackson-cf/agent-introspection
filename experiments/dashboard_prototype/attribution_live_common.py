"""Attribution specializations of the generic live evidence contract."""

from __future__ import annotations

from experiments.dashboard_prototype.attribution_common import (
    AttributionExperimentId,
    AttributionExperimentProof,
)
from experiments.dashboard_prototype.experiment_live_common import (
    LiveExperimentEvidence as GenericLiveExperimentEvidence,
)
from experiments.dashboard_prototype.experiment_live_common import LiveProofRequest
from experiments.dashboard_prototype.experiment_live_common import (
    RemoteCalculationPrimitive as GenericRemoteCalculationPrimitive,
)

__all__ = ("AttributionCalculationPrimitive", "AttributionLiveEvidence", "LiveProofRequest")


class AttributionCalculationPrimitive(GenericRemoteCalculationPrimitive[AttributionExperimentId]):
    """Attribution scalar input row."""


class AttributionLiveEvidence(GenericLiveExperimentEvidence[AttributionExperimentId]):
    """Attribution local proof and remote-calculation evidence."""

    proof: AttributionExperimentProof
    primitives: tuple[AttributionCalculationPrimitive, ...]

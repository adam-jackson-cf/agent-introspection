"""Shared result envelope specialization for disposable request experiments."""

from __future__ import annotations

from enum import StrEnum

from experiments.dashboard_prototype.experiment_common import ExperimentProof
from experiments.dashboard_prototype.experiment_live_common import LiveExperimentEvidence


class RequestExperimentId(StrEnum):
    """Registered request experiment identities."""

    FIELD_AUDIT = "E-Request-1"
    REQUEST_ATTEMPT = "E-Request-2"
    REMOTE_CALCULATION = "E-Request-3"


RequestExperimentProof = ExperimentProof[RequestExperimentId]
RequestLiveEvidence = LiveExperimentEvidence[RequestExperimentId]

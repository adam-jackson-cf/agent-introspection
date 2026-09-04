"""Shared result envelope specialization for disposable task experiments."""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from experiments.dashboard_prototype.experiment_common import ExperimentProof
from experiments.dashboard_prototype.experiment_live_common import LiveExperimentEvidence


class TaskExperimentId(StrEnum):
    """Registered task experiment identities."""

    AVAILABILITY = "E-Task-0"
    FIELD_AUDIT = "E-Task-1"
    ORDERED_REDUCER = "E-Task-2"
    TERMINAL_BOUNDARY = "E-Task-3"
    REMOTE_CALCULATION = "E-Task-4"


SUPPORTED_SURFACES: Final[tuple[tuple[str, str], ...]] = (
    ("omp", "omp"),
    ("codex-cli", "codex-cli"),
    ("codex-app-server", "codex-app-server"),
)
TASK_MEASURES: Final[tuple[str, ...]] = (
    "M2",
    "M3",
    "M4",
    "M5",
    "M7",
    "M8",
    "M9",
    "M10",
    "M11",
    "M13",
    "M16",
)


TaskExperimentProof = ExperimentProof[TaskExperimentId]
TaskLiveEvidence = LiveExperimentEvidence[TaskExperimentId]

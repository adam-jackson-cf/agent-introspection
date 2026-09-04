"""Generic privacy-safe contract for live experiment evidence."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from experiments.dashboard_prototype.experiment_common import ExperimentProof

_SAFE_NAME: Final[re.Pattern[str]] = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z", re.ASCII)
_SAFE_VALUE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z", re.ASCII)
_PROHIBITED: Final[tuple[str, ...]] = (
    "prompt",
    "response",
    "transcript",
    "command",
    "secret",
    "credential",
    "path",
    "native_session",
    "claude",
)


@dataclass(frozen=True, slots=True)
class LiveProofRequest:
    """Exact bounded fresh-real request shared by every live extractor."""

    run_id: str
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if not self.run_id or not _SAFE_VALUE.fullmatch(self.run_id):
            raise ValueError("live proof run ID must be an allowlisted token")
        if (
            self.start.tzinfo is None
            or self.start.utcoffset() is None
            or self.end.tzinfo is None
            or self.end.utcoffset() is None
            or self.start >= self.end
        ):
            raise ValueError("live proof window must be ordered timezone-aware instants")

    @property
    def source_boundary(self) -> str:
        return f"{self.start.isoformat()}..{self.end.isoformat()}"


@dataclass(frozen=True, slots=True)
class RemoteCalculationPrimitive[ExperimentIdT: StrEnum]:
    """One allowlisted scalar input row for a fixed remote calculation."""

    experiment_id: ExperimentIdT
    source_time: datetime
    ordinal: int
    dimensions: dict[str, str | int | bool]
    measures: dict[str, int | float]

    def __post_init__(self) -> None:
        if self.source_time.tzinfo is None or self.source_time.utcoffset() is None:
            raise ValueError("primitive source time must be timezone-aware")
        if isinstance(self.ordinal, bool) or self.ordinal < 0:
            raise ValueError("primitive ordinal must be a nonnegative integer")
        _validate_scalar_map(self.dimensions, measures=False)
        _validate_scalar_map(self.measures, measures=True)
        if not self.dimensions and not self.measures:
            raise ValueError("primitive requires an allowlisted calculation input")
        object.__setattr__(self, "dimensions", MappingProxyType(dict(self.dimensions)))
        object.__setattr__(self, "measures", MappingProxyType(dict(self.measures)))


@dataclass(frozen=True, slots=True)
class LiveExperimentEvidence[ExperimentIdT: StrEnum]:
    """A typed local proof and its exact remote-calculation input population."""

    proof: ExperimentProof[ExperimentIdT]
    primitives: tuple[RemoteCalculationPrimitive[ExperimentIdT], ...]
    remote_query_id: str | None
    remote_oracle: Mapping[str, Mapping[str, int | float]] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        if any(row.experiment_id is not self.proof.experiment_id for row in self.primitives):
            raise ValueError("primitive experiment identity must match its proof")
        if self.remote_query_id is not None and not _SAFE_VALUE.fullmatch(self.remote_query_id):
            raise ValueError("remote query identity must be an allowlisted token")
        oracle = {
            cohort: MappingProxyType(dict(metrics))
            for cohort, metrics in self.remote_oracle.items()
        }
        for cohort, metrics in oracle.items():
            if not _SAFE_VALUE.fullmatch(cohort):
                raise ValueError("remote oracle cohort identity must be an allowlisted token")
            _validate_scalar_map(metrics, measures=True)
        object.__setattr__(self, "remote_oracle", MappingProxyType(oracle))
        if self.proof.result.value == "Proven" and (
            not self.primitives or self.remote_query_id is None
        ):
            raise ValueError("Proven local evidence requires remote calculation inputs")


def _validate_scalar_map(values: Mapping[str, str | int | float | bool], *, measures: bool) -> None:
    for key, value in values.items():
        lowered = key.lower()
        if not _SAFE_NAME.fullmatch(key) or any(word in lowered for word in _PROHIBITED):
            raise ValueError("primitive field name is not allowlisted")
        if measures:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("primitive measures must be numeric")
        elif isinstance(value, str) and not _SAFE_VALUE.fullmatch(value):
            raise ValueError("primitive dimension value is not allowlisted")

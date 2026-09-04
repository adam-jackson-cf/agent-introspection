"""Shared result envelope specialization for disposable pipeline experiments."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from experiments.dashboard_prototype.contracts import ExperimentResult
from experiments.dashboard_prototype.experiment_common import ExperimentProof


class PipelineExperimentId(StrEnum):
    """Registered pipeline experiment identities."""

    SNAPSHOT = "E-Pipeline-1"
    TERMINAL_CADENCE = "E-Pipeline-2"
    SOURCE_LAG = "E-Pipeline-3"
    INTEGRITY = "E-Pipeline-4"
    OUTBOX = "E-Pipeline-5"
    LEDGER = "E-Pipeline-6"


PipelineExperimentProof = ExperimentProof[PipelineExperimentId]


@dataclass(frozen=True, slots=True)
class PipelineExecutionAuthority:
    """Per-experiment execution binding; blocked proofs retain no evidence bundle."""

    proof: PipelineExperimentProof
    primitive_event_ids: Sequence[str]
    result_event_id: str
    remote_query_id: str | None
    local_oracle: Mapping[str, object]
    remote_result: Mapping[str, object]
    delivery: Mapping[str, int]

    def __post_init__(self) -> None:
        if not self.result_event_id or any(
            not identifier for identifier in self.primitive_event_ids
        ):
            raise ValueError("pipeline execution event IDs must be exact")
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in self.delivery.values()
        ):
            raise ValueError("pipeline execution delivery counts must be integers")
        if self.proof.result is not ExperimentResult.PROVEN:
            if (
                self.primitive_event_ids
                or self.remote_query_id is not None
                or self.local_oracle
                or self.remote_result
            ):
                raise ValueError("blocked pipeline proof must retain a null evidence bundle")
        elif (
            not self.proof.evidence_ids
            or not self.primitive_event_ids
            or self.remote_query_id is None
            or not self.local_oracle
            or not self.remote_result
            or not _typed_equal(self.local_oracle, self.remote_result)
            or (
                self.delivery.get("primitive_selected"),
                self.delivery.get("primitive_delivered"),
                self.delivery.get("result_selected"),
                self.delivery.get("result_delivered"),
            )
            != (len(self.primitive_event_ids), len(self.primitive_event_ids), 1, 1)
        ):
            raise ValueError("proven pipeline proof requires a complete matching authority")
        object.__setattr__(self, "primitive_event_ids", tuple(self.primitive_event_ids))
        object.__setattr__(self, "local_oracle", MappingProxyType(dict(self.local_oracle)))
        if self.proof.result is ExperimentResult.PROVEN:
            _validate_scalar_evidence(self.local_oracle)
            _validate_scalar_evidence(self.remote_result)
        object.__setattr__(self, "remote_result", MappingProxyType(dict(self.remote_result)))
        object.__setattr__(self, "delivery", MappingProxyType(dict(self.delivery)))

    def payload(self) -> dict[str, object]:
        if self.proof.result is not ExperimentResult.PROVEN:
            return {
                "blocked_boundaries": list(self.proof.blocked_boundaries),
                "evidence_bundle": None,
            }
        return {
            "source_boundary": self.proof.source_boundary,
            "reducer_hash": self.proof.content_hash(),
            "primitive_event_ids": list(self.primitive_event_ids),
            "result_event_id": self.result_event_id,
            "remote_query_id": self.remote_query_id,
            "local_oracle": dict(sorted(self.local_oracle.items())),
            "remote_result": dict(sorted(self.remote_result.items())),
            "delivery": dict(sorted(self.delivery.items())),
        }


def _validate_scalar_evidence(value: object) -> None:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) or not key for key in value):
            raise ValueError("pipeline evidence keys must be exact strings")
        for nested in value.values():
            _validate_scalar_evidence(nested)
        return
    if not isinstance(value, (str, int, float)) or (
        isinstance(value, float) and not math.isfinite(value)
    ):
        raise ValueError("pipeline evidence must contain exact scalar values")


def _typed_equal(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, Mapping):
        return (
            isinstance(actual, Mapping)
            and set(actual) == set(expected)
            and all(_typed_equal(actual[key], value) for key, value in expected.items())
        )
    return actual == expected

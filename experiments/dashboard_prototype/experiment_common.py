"""Generic machine-readable envelope for disposable experiments."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from experiments.dashboard_prototype.contracts import (
    EXPERIMENT_NAMESPACE,
    EvidenceProvenance,
    ExperimentResult,
    PrototypeContractError,
)

type JsonScalar = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class ExperimentProof[ExperimentIdT: StrEnum]:
    """Common machine-readable envelope; experiment modules own their invariants."""

    experiment_id: ExperimentIdT
    run_id: str
    result: ExperimentResult
    provenance: EvidenceProvenance
    source_boundary: str
    metrics: Mapping[str, JsonScalar]
    assertions: Mapping[str, bool]
    evidence_ids: Sequence[str]
    blocked_boundaries: Sequence[str]
    proposal: str

    def __post_init__(self) -> None:
        if not self.run_id or not self.source_boundary or not self.proposal:
            raise PrototypeContractError("experiment proof metadata must be nonempty")
        if not self.assertions or any(
            not isinstance(name, str) or not name or not isinstance(passed, bool)
            for name, passed in self.assertions.items()
        ):
            raise PrototypeContractError("experiment proof assertions must be named booleans")
        if any(not isinstance(name, str) or not name for name in self.metrics):
            raise PrototypeContractError("experiment proof metric names must be nonempty")
        if any(not isinstance(identity, str) or not identity for identity in self.evidence_ids):
            raise PrototypeContractError("experiment proof evidence identities must be nonempty")
        if any(
            not isinstance(boundary, str) or not boundary for boundary in self.blocked_boundaries
        ):
            raise PrototypeContractError("experiment proof blocked boundaries must be nonempty")
        if self.result is ExperimentResult.PROVEN:
            if self.provenance is not EvidenceProvenance.FRESH_REAL:
                raise PrototypeContractError(
                    "Proven experiment proof requires fresh-real provenance"
                )
            if not all(self.assertions.values()) or self.blocked_boundaries:
                raise PrototypeContractError(
                    "Proven experiment proof requires every assertion and no blocked boundary"
                )
        if self.result is ExperimentResult.BLOCKED and not self.blocked_boundaries:
            raise PrototypeContractError("Blocked experiment proof requires a missing boundary")
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))
        object.__setattr__(self, "assertions", MappingProxyType(dict(self.assertions)))
        object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids))
        object.__setattr__(self, "blocked_boundaries", tuple(self.blocked_boundaries))

    def canonical_json(self) -> str:
        """Return deterministic proof JSON without unrestricted source payloads."""
        payload = {
            "experiment_namespace": EXPERIMENT_NAMESPACE,
            "experiment_id": self.experiment_id.value,
            "run_id": self.run_id,
            "result": self.result.value,
            "provenance": self.provenance.value,
            "source_boundary": self.source_boundary,
            "metrics": dict(self.metrics),
            "assertions": dict(self.assertions),
            "evidence_ids": list(self.evidence_ids),
            "blocked_boundaries": list(self.blocked_boundaries),
            "proposal": self.proposal,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def content_hash(self) -> str:
        """Return the immutable hash of the canonical proof JSON."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

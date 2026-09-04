"""Retained, privacy-safe unsupported-boundary proof for E-Attribution-3."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from experiments.dashboard_prototype.attribution_common import (
    AttributionExperimentId,
    AttributionExperimentProof,
)
from experiments.dashboard_prototype.attribution_live_common import AttributionLiveEvidence
from experiments.dashboard_prototype.contracts import EvidenceProvenance, ExperimentResult
from experiments.dashboard_prototype.experiment_live_common import LiveProofRequest

_PRODUCER: Final = "claude-code"
_REOPENING_CONDITION: Final = "hook session ID = local artifact session ID = OTEL session ID"
_LEGACY_REOPENING_CONDITION: Final = "hook session_id = local artifact session ID = OTEL session.id"
_BLOCKED_BOUNDARY: Final = "three-way-identity-authority"


@dataclass(frozen=True, slots=True)
class ClaudeBoundaryEvidence:
    """Redacted immutable record of the retained unsupported boundary."""

    evidence_id: str
    producer: str
    reopening_condition: str
    ingestion_enabled: bool

    def __post_init__(self) -> None:
        if len(self.evidence_id) != 16 or any(
            character not in "0123456789abcdef" for character in self.evidence_id
        ):
            raise ValueError("Claude boundary evidence ID must be a short SHA-256 hash")
        if self.producer != _PRODUCER:
            raise ValueError("Claude boundary evidence must use the canonical producer")
        if self.reopening_condition != _REOPENING_CONDITION:
            raise ValueError("Claude boundary reopening condition is not canonical")
        if self.ingestion_enabled:
            raise ValueError("unsupported Claude boundary must keep ingestion disabled")


def load_claude_boundary_evidence(path: Path) -> ClaudeBoundaryEvidence:
    """Load one retained mismatch record without retaining native identifiers."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Claude boundary fixture is unreadable") from error
    if not isinstance(document, Mapping):
        raise ValueError("Claude boundary fixture must be an object")
    unsupported = document.get("unsupported")
    if not isinstance(unsupported, list):
        raise ValueError("Claude boundary fixture lacks unsupported records")
    records = [
        record
        for record in unsupported
        if isinstance(record, Mapping) and record.get("producer") == _PRODUCER
    ]
    if len(records) != 1:
        raise ValueError("Claude boundary fixture must contain exactly one Claude record")
    record = records[0]
    condition = record.get("missing_equality_boundary")
    ingestion_enabled = record.get("source_ingestion_enabled")
    if condition not in {_REOPENING_CONDITION, _LEGACY_REOPENING_CONDITION}:
        raise ValueError("Claude boundary fixture has an unknown reopening condition")
    if ingestion_enabled is not False:
        raise ValueError("Claude boundary fixture contradicts disabled ingestion")
    if record.get("fresh_installed_authority", False) is not False:
        raise ValueError("retained mismatch cannot claim fresh installed authority")
    evidence_id = _short_hash(_PRODUCER, _REOPENING_CONDITION, "ingestion-disabled")
    return ClaudeBoundaryEvidence(
        evidence_id=evidence_id,
        producer=_PRODUCER,
        reopening_condition=_REOPENING_CONDITION,
        ingestion_enabled=False,
    )


def build_claude_boundary_proof(
    run_id: str, evidence: ClaudeBoundaryEvidence
) -> AttributionExperimentProof:
    """Build the permanently blocked proof until installed three-way authority exists."""
    return AttributionExperimentProof(
        experiment_id=AttributionExperimentId.CLAUDE_BOUNDARY,
        run_id=run_id,
        result=ExperimentResult.BLOCKED,
        provenance=EvidenceProvenance.RETAINED,
        source_boundary=f"retained:{evidence.evidence_id}",
        metrics={
            "supported_denominator_population": 0,
            "remote_primitive_count": 0,
        },
        assertions={
            "ingestion_disabled": not evidence.ingestion_enabled,
            "reopening_condition_recorded": (evidence.reopening_condition == _REOPENING_CONDITION),
            "fresh_installed_three_way_authority": False,
            "retained_evidence_redacted": True,
        },
        evidence_ids=(evidence.evidence_id,),
        blocked_boundaries=(_BLOCKED_BOUNDARY,),
        proposal=_REOPENING_CONDITION,
    )


def extract_claude_boundary(path: Path, request: LiveProofRequest) -> AttributionLiveEvidence:
    """Return retained E-Attribution-3 evidence with no remote calculation rows."""
    evidence = load_claude_boundary_evidence(path)
    proof = build_claude_boundary_proof(request.run_id, evidence)
    return AttributionLiveEvidence(proof, (), None)


def _short_hash(*parts: str) -> str:
    material = "\x1f".join(parts).encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:16]

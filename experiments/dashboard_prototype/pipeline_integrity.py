"""Pure, redacted integrity reducer for E-Pipeline-4."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType

from experiments.dashboard_prototype.contracts import (
    EvidenceProvenance,
    ExperimentResult,
    PrototypeContractError,
)
from experiments.dashboard_prototype.pipeline_common import (
    PipelineExperimentId,
    PipelineExperimentProof,
)

_SAFE_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,47}\Z")


class IntegrityInvariant(StrEnum):
    """The closed set of durable pipeline integrity failures."""

    DURABLE_INTEGRITY_FAILURE = "durable-integrity-failure"
    CANONICAL_LIFECYCLE_CONTEXT_REJECTION = "canonical-lifecycle-context-rejection"
    DETERMINISTIC_ID_CONFLICT = "deterministic-id-conflict"
    VERSION_GAP = "version-gap"
    CONFLICTING_IDENTITY = "conflicting-identity"


@dataclass(frozen=True, slots=True)
class IntegrityObservation:
    """An already-allowlisted durable anomaly, containing no source payload."""

    invariant: IntegrityInvariant
    event_or_projection: str
    source_time: datetime
    producer: str
    runtime: str
    cohort_short_identity: str
    affected_short_identity: str

    def __post_init__(self) -> None:
        if not isinstance(self.invariant, IntegrityInvariant):
            raise PrototypeContractError("integrity invariant must be registered")
        if self.source_time.tzinfo is None:
            raise PrototypeContractError("integrity source time must be timezone-aware")
        for value in (
            self.event_or_projection,
            self.producer,
            self.runtime,
            self.cohort_short_identity,
            self.affected_short_identity,
        ):
            if not _is_safe_token(value):
                raise PrototypeContractError(
                    "integrity observations require redacted identifier tokens only"
                )


@dataclass(frozen=True, slots=True)
class P11Incident:
    """Immutable redacted incident suitable for a dashboard projection."""

    invariant: IntegrityInvariant
    event_or_projection: str
    source_time: datetime
    producer: str
    runtime: str
    affected_short_identity: str
    incident_id: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.invariant, IntegrityInvariant)
            or self.source_time.tzinfo is None
            or not self.incident_id.startswith("p11-")
            or len(self.incident_id) != 28
        ):
            raise PrototypeContractError("P11 incident must have registered redacted fields")
        for value in (
            self.event_or_projection,
            self.producer,
            self.runtime,
            self.affected_short_identity,
        ):
            if not _is_safe_token(value):
                raise PrototypeContractError("P11 incident must have registered redacted fields")


@dataclass(frozen=True, slots=True)
class IntegrityReduction:
    """Selected incidents and only safe, remote-calculation count dimensions."""

    incidents: tuple[P11Incident, ...]
    selected_row_count: int
    duplicate_row_count: int
    withheld_cohorts: tuple[str, ...]
    aggregate_counts: Mapping[str, int]

    def __post_init__(self) -> None:
        if self.selected_row_count != len(self.incidents) + self.duplicate_row_count:
            raise PrototypeContractError("integrity duplicate accounting must conserve rows")
        object.__setattr__(self, "aggregate_counts", MappingProxyType(dict(self.aggregate_counts)))


def reduce_integrity_incidents(
    rows: Sequence[IntegrityObservation], *, start: datetime, end: datetime
) -> IntegrityReduction:
    """Project canonical ``start < source_time <= end`` incidents without side effects."""
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise PrototypeContractError("integrity range must be increasing and timezone-aware")

    selected = [row for row in rows if start < row.source_time <= end]
    unique: dict[str, IntegrityObservation] = {}
    duplicate_count = 0
    for row in selected:
        incident_id = _incident_id(row)
        existing = unique.get(incident_id)
        if existing is None:
            unique[incident_id] = row
        elif existing != row:
            raise PrototypeContractError("deterministic incident identifier collision")
        else:
            duplicate_count += 1

    withheld = {
        row.cohort_short_identity
        for row in unique.values()
        if row.invariant
        in {
            IntegrityInvariant.DURABLE_INTEGRITY_FAILURE,
            IntegrityInvariant.DETERMINISTIC_ID_CONFLICT,
            IntegrityInvariant.VERSION_GAP,
            IntegrityInvariant.CONFLICTING_IDENTITY,
        }
    }
    retained = [row for row in unique.values() if row.cohort_short_identity not in withheld]
    incidents = tuple(
        sorted(
            (_incident(row) for row in unique.values()),
            key=lambda incident: (incident.source_time, incident.incident_id),
        )
    )
    counts = Counter(_metric_name(row.invariant, row.producer, row.runtime) for row in retained)
    return IntegrityReduction(
        incidents=incidents,
        selected_row_count=len(selected),
        duplicate_row_count=duplicate_count,
        withheld_cohorts=tuple(sorted(withheld)),
        aggregate_counts=dict(sorted(counts.items())),
    )


def build_integrity_proof(
    reduction: IntegrityReduction,
    *,
    run_id: str | None,
    provenance: EvidenceProvenance | None,
    source_boundary: str | None,
    expected_remote_counts: Mapping[str, int] | None,
) -> PipelineExperimentProof:
    """Build a proof, blocking missing boundaries and failing falsified reconciliation."""
    missing = tuple(
        boundary
        for boundary, value in (
            ("exact run_id", run_id),
            ("exact provenance", provenance),
            ("exact source boundary", source_boundary),
            ("bounded remote calculation counts", expected_remote_counts),
        )
        if value is None or value == ""
    )
    if missing:
        withheld_boundaries = tuple(
            f"withheld-corrupt-cohort:{cohort}" for cohort in reduction.withheld_cohorts
        )
        return PipelineExperimentProof(
            experiment_id=PipelineExperimentId.INTEGRITY,
            run_id=run_id or "missing-run-id",
            result=ExperimentResult.BLOCKED,
            provenance=provenance or EvidenceProvenance.RETAINED,
            source_boundary=source_boundary or "missing-source-boundary",
            metrics=dict(reduction.aggregate_counts),
            assertions={
                "required_boundaries_present": False,
                "conflicting_or_corrupt_cohorts_withheld": True,
            },
            evidence_ids=tuple(incident.incident_id for incident in reduction.incidents),
            blocked_boundaries=missing + withheld_boundaries,
            proposal="Project only redacted P11 integrity incidents after reconciliation.",
        )

    if (
        run_id is None
        or provenance is None
        or source_boundary is None
        or expected_remote_counts is None
    ):
        raise AssertionError("present proof boundaries must be non-null")
    expected = dict(expected_remote_counts)
    safe_metrics = all(
        _is_safe_token(name.replace(".", ":")) and isinstance(value, int) and value >= 0
        for name, value in reduction.aggregate_counts.items()
    )
    withheld_boundaries = tuple(
        f"withheld-corrupt-cohort:{cohort}" for cohort in reduction.withheld_cohorts
    )
    assertions = {
        "canonical_selected_range": True,
        "duplicate_rows_conserved": reduction.selected_row_count
        == len(reduction.incidents) + reduction.duplicate_row_count,
        "conflicting_or_corrupt_cohorts_withheld": True,
        "safe_remote_metrics": safe_metrics,
        "fresh_real_provenance": provenance is EvidenceProvenance.FRESH_REAL,
        "remote_counts_reconciled": expected == dict(reduction.aggregate_counts),
        "no_unresolved_integrity_incidents": not reduction.withheld_cohorts,
    }
    result = (
        ExperimentResult.BLOCKED
        if withheld_boundaries
        else (
            ExperimentResult.PROVEN
            if provenance is EvidenceProvenance.FRESH_REAL and all(assertions.values())
            else ExperimentResult.FAILED
        )
    )
    return PipelineExperimentProof(
        experiment_id=PipelineExperimentId.INTEGRITY,
        run_id=run_id,
        result=result,
        provenance=provenance,
        source_boundary=source_boundary,
        metrics=dict(reduction.aggregate_counts),
        assertions=assertions,
        evidence_ids=tuple(incident.incident_id for incident in reduction.incidents),
        blocked_boundaries=withheld_boundaries,
        proposal="Project only redacted P11 integrity incidents after reconciliation.",
    )


def _incident(row: IntegrityObservation) -> P11Incident:
    return P11Incident(
        invariant=row.invariant,
        event_or_projection=row.event_or_projection,
        source_time=row.source_time,
        producer=row.producer,
        runtime=row.runtime,
        affected_short_identity=row.affected_short_identity,
        incident_id=_incident_id(row),
    )


def _incident_id(row: IntegrityObservation) -> str:
    fields = (
        row.invariant.value,
        row.event_or_projection,
        row.source_time.isoformat(),
        row.producer,
        row.runtime,
        row.cohort_short_identity,
        row.affected_short_identity,
    )
    return "p11-" + hashlib.sha256("\x1f".join(fields).encode()).hexdigest()[:24]


def _is_safe_token(value: str) -> bool:
    """Reject free text, path-like data, secrets, and full UUID-shaped identities."""
    lowered = value.lower()
    return bool(
        _SAFE_TOKEN.fullmatch(value)
        and not re.fullmatch(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", lowered)
        and not lowered.startswith(("sk-", "token-", "secret-", "bearer-"))
    )


def _metric_name(invariant: IntegrityInvariant, producer: str, runtime: str) -> str:
    return f"p11.{invariant.value}.{producer}.{runtime}"

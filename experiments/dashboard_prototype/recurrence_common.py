"""Strict privacy-safe contracts for recurrence proof experiments."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from experiments.dashboard_prototype.contracts import (
    EXPERIMENT_NAMESPACE,
    EvidenceProvenance,
    ExperimentResult,
    PrototypeContractError,
)

_SAFE_NAME: Final[re.Pattern[str]] = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z", re.ASCII)
_SAFE_VALUE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z", re.ASCII)
_INT64_MAX: Final[int] = 2**63 - 1
_NANOSECONDS_PER_SECOND: Final[int] = 1_000_000_000
_FROZEN_PRIVACY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "experiment_id",
        "run_id",
        "producer",
        "surface",
        "installed_version",
        "capability_state",
        "bounded_start_ns",
        "bounded_end_ns",
        "source_extraction_upper_bound_ns",
        "native_session_id",
        "stable_identity",
        "event_id",
        "event_version",
        "timestamp_ns",
        "interval_start_ns",
        "interval_end_ns",
        "project_id_or_unresolved_state",
        "provider_identity",
        "model_identity",
        "request_identity",
        "attempt_identity",
        "accounting_identity",
        "terminal_outcome",
        "error_class",
        "status_code",
        "retry_reason",
        "token_counts",
        "normalized_operation",
        "allowlisted_normalized_target",
        "detector_fingerprint",
        "policy_identity",
        "rejection_reason",
        "reducer_counts",
        "conservation_equation",
        "remote_event_id",
        "remote_query_result",
        "oracle_result",
        "event_id_ordinal",
    }
)
_NUMERIC_MEASURE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "reducer_counts",
        "conservation_equation",
        "remote_query_result",
        "oracle_result",
        "token_counts",
    }
)
_FROZEN_DIMENSION_KEYS: Final[frozenset[str]] = _FROZEN_PRIVACY_KEYS - _NUMERIC_MEASURE_KEYS
_UNIX_EPOCH: Final[datetime] = datetime(1970, 1, 1, tzinfo=UTC)
_PROHIBITED_PRIVACY_TERMS: Final[tuple[str, ...]] = (
    "prompt",
    "response",
    "transcript",
    "command",
    "output",
    "payload",
    "secret",
    "credential",
    "password",
    "token",
    "path",
    "session",
)
_MISSING_DURABLE_AUTHORITY = "missing_durable_authority"
_CONTRADICTION_DETECTED = "contradiction_detected"
_REMOTE_CALCULATION_RECONCILED = "remote_calculation_reconciled"


class RecurrenceExperimentId(StrEnum):
    """Registered identities for recurrence experiments."""

    WAVE_A = "E-Recurrence-0"
    FINDING_PROJECTION = "E-Recurrence-1"
    SUCCESSFUL_PRACTICE = "E-Recurrence-2"
    RULE_ADHERENCE = "E-Recurrence-3"
    INTERVENTION_AUDIT = "E-Recurrence-4"


SUPPORTED_SURFACES: Final[tuple[tuple[str, str], ...]] = (
    ("omp", "omp"),
    ("codex-cli", "codex-cli"),
    ("codex-app-server", "codex-app-server"),
)

type JsonMetric = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class RecurrenceExperimentProof:
    """Immutable result envelope for one bounded recurrence experiment run."""

    experiment_id: RecurrenceExperimentId
    run_id: str
    result: ExperimentResult
    provenance: EvidenceProvenance
    source_start: datetime
    source_end: datetime
    source_boundary: str
    metrics: Mapping[str, JsonMetric]
    assertions: Mapping[str, bool]
    evidence_ids: Sequence[str]
    blocked_boundaries: Sequence[str]
    proposal: str

    def __post_init__(self) -> None:
        _validate_proof_metadata(self)
        evidence_ids = _validate_tokens(self.evidence_ids, "evidence IDs")
        blocked_boundaries = _validate_tokens(self.blocked_boundaries, "blocked boundaries")
        _validate_result_contract(self, evidence_ids, blocked_boundaries)
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))
        object.__setattr__(self, "assertions", MappingProxyType(dict(self.assertions)))
        object.__setattr__(self, "evidence_ids", evidence_ids)
        object.__setattr__(self, "blocked_boundaries", blocked_boundaries)

    def canonical_json(self) -> str:
        """Return deterministic, privacy-safe JSON suitable for content hashing."""
        payload = {
            "experiment_namespace": EXPERIMENT_NAMESPACE,
            "experiment_id": self.experiment_id.value,
            "run_id": self.run_id,
            "result": self.result.value,
            "provenance": self.provenance.value,
            "source_start": self.source_start.isoformat(),
            "source_end": self.source_end.isoformat(),
            "source_boundary": self.source_boundary,
            "metrics": dict(self.metrics),
            "assertions": dict(self.assertions),
            "evidence_ids": list(self.evidence_ids),
            "blocked_boundaries": list(self.blocked_boundaries),
            "proposal": self.proposal,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def content_hash(self) -> str:
        """Return the SHA-256 digest of this proof's canonical JSON."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RecurrencePrimitive:
    """One privacy-safe scalar row in a bounded recurrence source population."""

    experiment_id: RecurrenceExperimentId
    source_time: datetime
    ordinal: int
    dimensions: Mapping[str, str | int | bool]
    measures: Mapping[str, int | float]
    source_time_ns: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.experiment_id, RecurrenceExperimentId):
            raise ValueError("recurrence primitive experiment ID must be registered")
        if (
            not isinstance(self.source_time, datetime)
            or self.source_time.tzinfo is None
            or self.source_time.utcoffset() is None
        ):
            raise ValueError("recurrence primitive source time must be timezone-aware")
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or self.ordinal < 0:
            raise ValueError("recurrence primitive ordinal must be a nonnegative integer")
        _validate_source_time_ns(self.source_time_ns)
        _validate_scalar_map(self.dimensions, measures=False)
        _validate_scalar_map(self.measures, measures=True)
        if not self.dimensions and not self.measures:
            raise ValueError("recurrence primitive requires a privacy-safe scalar input")
        object.__setattr__(self, "dimensions", MappingProxyType(dict(self.dimensions)))
        object.__setattr__(self, "measures", MappingProxyType(dict(self.measures)))


@dataclass(frozen=True, slots=True)
class RecurrenceLiveEvidence:
    """A bounded recurrence proof and its exact ordered source population."""

    proof: RecurrenceExperimentProof
    primitives: Sequence[RecurrencePrimitive]
    remote_calculation_id: str | None
    finding_version_source_ids: Sequence[str] = ()
    canonical_task_membership_source_ids: Sequence[str] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.proof, RecurrenceExperimentProof):
            raise ValueError("recurrence live evidence requires a recurrence proof")
        if isinstance(self.primitives, str) or not isinstance(self.primitives, Sequence):
            raise ValueError("recurrence primitives must be a sequence")
        primitives = tuple(self.primitives)
        if any(not isinstance(row, RecurrencePrimitive) for row in primitives):
            raise ValueError("recurrence evidence must contain recurrence primitives")
        if any(row.experiment_id is not self.proof.experiment_id for row in primitives):
            raise ValueError("recurrence primitive experiment identity must match its proof")
        if any(
            not _is_within_source_window(row, self.proof.source_start, self.proof.source_end)
            for row in primitives
        ):
            raise ValueError("recurrence primitive must satisfy source membership >start <=end")
        if self.remote_calculation_id is not None:
            _validate_token(self.remote_calculation_id, "remote calculation ID")
        finding_version_source_ids = _validate_tokens(
            self.finding_version_source_ids, "finding-version source IDs"
        )
        canonical_task_membership_source_ids = _validate_tokens(
            self.canonical_task_membership_source_ids,
            "canonical-task-membership source IDs",
        )
        if self.proof.result is ExperimentResult.PROVEN and (
            not primitives or self.remote_calculation_id is None
        ):
            raise ValueError(
                "Proven recurrence evidence requires primitives and remote calculation"
            )
        object.__setattr__(self, "primitives", primitives)
        object.__setattr__(self, "finding_version_source_ids", finding_version_source_ids)
        object.__setattr__(
            self,
            "canonical_task_membership_source_ids",
            canonical_task_membership_source_ids,
        )


def _validate_window(start: datetime, end: datetime) -> None:
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        raise PrototypeContractError("recurrence proof source window must be datetimes")
    if (
        start.tzinfo is None
        or start.utcoffset() is None
        or end.tzinfo is None
        or end.utcoffset() is None
        or start >= end
    ):
        raise PrototypeContractError(
            "recurrence proof source window must be ordered timezone-aware"
        )


def _validate_proof_metadata(proof: RecurrenceExperimentProof) -> None:
    if not isinstance(proof.experiment_id, RecurrenceExperimentId):
        raise PrototypeContractError("recurrence proof experiment ID must be registered")
    if not isinstance(proof.result, ExperimentResult):
        raise PrototypeContractError("recurrence proof result must be an ExperimentResult")
    if not isinstance(proof.provenance, EvidenceProvenance):
        raise PrototypeContractError("recurrence proof provenance must be an EvidenceProvenance")
    _validate_token(proof.run_id, "run ID")
    if not isinstance(proof.source_boundary, str) or not proof.source_boundary.strip():
        raise PrototypeContractError("recurrence proof source boundary must be nonempty")
    if not isinstance(proof.proposal, str) or not proof.proposal.strip():
        raise PrototypeContractError("recurrence proof proposal must be nonempty")
    _validate_window(proof.source_start, proof.source_end)
    _validate_metrics(proof.metrics)
    _validate_assertions(proof.assertions)


def _validate_result_contract(
    proof: RecurrenceExperimentProof,
    evidence_ids: tuple[str, ...],
    blocked_boundaries: tuple[str, ...],
) -> None:
    if proof.result not in {
        ExperimentResult.PROVEN,
        ExperimentResult.BLOCKED,
        ExperimentResult.FAILED,
    }:
        raise PrototypeContractError("recurrence proof result must be Proven, Blocked, or Failed")
    if proof.result is ExperimentResult.PROVEN:
        _validate_proven_contract(proof, evidence_ids, blocked_boundaries)
    elif proof.result is ExperimentResult.BLOCKED:
        if _MISSING_DURABLE_AUTHORITY not in blocked_boundaries:
            raise PrototypeContractError(
                "Blocked recurrence proof must name missing_durable_authority"
            )
    elif proof.assertions.get(_CONTRADICTION_DETECTED) is not True:
        raise PrototypeContractError(
            "Failed recurrence proof must name contradiction_detected=true"
        )


def _validate_proven_contract(
    proof: RecurrenceExperimentProof,
    evidence_ids: tuple[str, ...],
    blocked_boundaries: tuple[str, ...],
) -> None:
    if proof.provenance is not EvidenceProvenance.FRESH_REAL:
        raise PrototypeContractError("Proven recurrence proof requires fresh-real provenance")
    if not evidence_ids:
        raise PrototypeContractError("Proven recurrence proof requires exact evidence IDs")
    if proof.assertions.get(_REMOTE_CALCULATION_RECONCILED) is not True:
        raise PrototypeContractError(
            "Proven recurrence proof requires remote_calculation_reconciled=true"
        )
    if blocked_boundaries or not all(proof.assertions.values()):
        raise PrototypeContractError(
            "Proven recurrence proof requires passing assertions and no blocked boundary"
        )


def _validate_metrics(metrics: Mapping[str, JsonMetric]) -> None:
    if not isinstance(metrics, Mapping):
        raise PrototypeContractError("recurrence proof metrics must be a mapping")
    for name, value in metrics.items():
        _validate_field_name(name)
        if isinstance(value, float) and not math.isfinite(value):
            raise PrototypeContractError("recurrence proof metrics must be finite")
        if not isinstance(value, (str, int, float, bool, type(None))):
            raise PrototypeContractError("recurrence proof metrics must be JSON-safe scalars")
        if isinstance(value, str) and not _SAFE_VALUE.fullmatch(value):
            raise PrototypeContractError("recurrence proof metric strings must be allowlisted")


def _validate_assertions(assertions: Mapping[str, bool]) -> None:
    if not isinstance(assertions, Mapping) or not assertions:
        raise PrototypeContractError("recurrence proof assertions must be a nonempty mapping")
    for name, passed in assertions.items():
        _validate_field_name(name)
        if not isinstance(passed, bool):
            raise PrototypeContractError("recurrence proof assertions must be named booleans")


def _validate_source_time_ns(value: int | None) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _INT64_MAX:
        raise ValueError("recurrence primitive source_time_ns must be a nonnegative signed Int64")


def _datetime_to_ns(value: datetime) -> int:
    delta = value.astimezone(UTC) - _UNIX_EPOCH
    return (
        delta.days * 86_400 + delta.seconds
    ) * _NANOSECONDS_PER_SECOND + delta.microseconds * 1_000


def _is_within_source_window(
    primitive: RecurrencePrimitive, start: datetime, end: datetime
) -> bool:
    if primitive.source_time_ns is not None:
        return _datetime_to_ns(start) < primitive.source_time_ns <= _datetime_to_ns(end)
    return start < primitive.source_time <= end


def _validate_scalar_map(values: Mapping[str, str | int | float | bool], *, measures: bool) -> None:
    if not isinstance(values, Mapping):
        raise ValueError("recurrence primitive fields must be mappings")
    for name, value in values.items():
        _validate_primitive_field_name(name, measures=measures)
        if measures:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("recurrence primitive measures must be numeric")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("recurrence primitive measures must be finite")
        elif isinstance(value, str):
            if not _SAFE_VALUE.fullmatch(value):
                raise ValueError("recurrence primitive dimension values must be allowlisted")
        elif isinstance(value, (bool, int)):
            continue
        else:
            raise ValueError("recurrence primitive dimensions must be scalar")


def _validate_primitive_field_name(name: object, *, measures: bool) -> None:
    _validate_field_name(name)
    allowed_keys = _NUMERIC_MEASURE_KEYS if measures else _FROZEN_DIMENSION_KEYS
    if name not in allowed_keys:
        raise ValueError("recurrence primitive field name is not frozen-allowlisted")


def _validate_field_name(name: object) -> None:
    if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name):
        raise ValueError("recurrence field name is not allowlisted")
    if any(term in name.lower() for term in _PROHIBITED_PRIVACY_TERMS):
        raise ValueError("recurrence field name crosses the privacy boundary")


def _validate_token(value: object, label: str) -> None:
    if not isinstance(value, str) or not _SAFE_VALUE.fullmatch(value):
        raise PrototypeContractError(f"recurrence {label} must be an allowlisted token")


def _validate_tokens(values: Sequence[str], label: str) -> tuple[str, ...]:
    if isinstance(values, str) or not isinstance(values, Sequence):
        raise PrototypeContractError(f"recurrence {label} must be a sequence")
    result = tuple(values)
    for value in result:
        _validate_token(value, label[:-1] if label.endswith("s") else label)
    if len(set(result)) != len(result):
        raise PrototypeContractError(f"recurrence {label} must be unique")
    return result

"""Privacy-safe E-Request-1 field-authority classifications."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from experiments.dashboard_prototype.contracts import EvidenceProvenance, ExperimentResult
from experiments.dashboard_prototype.request_common import (
    RequestExperimentId,
    RequestExperimentProof,
)

PRODUCER_SURFACES: Final[tuple[tuple[str, str], ...]] = (
    ("omp", "omp"),
    ("codex-cli", "codex-cli"),
    ("codex-app-server", "codex-app-server"),
)
REQUIRED_REQUEST_FIELDS: Final[tuple[str, ...]] = (
    "producer",
    "surface",
    "native_session_id",
    "provider",
    "logical_request_id",
    "attempt_id",
    "attempt_index",
    "accounting_record_id",
    "deterministic_event_identity",
    "request_started_at",
    "provider_accepted_at",
    "first_token_at",
    "stream_started",
    "stream_ended_at",
    "terminal_at",
    "stream_completed",
    "requested_model",
    "response_model",
    "terminal_outcome",
    "provider_status",
    "provider_error_class",
    "provider_error_code",
    "retryable",
    "retry_membership",
    "retry_order",
    "retry_reason",
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "project_id",
    "project_state",
)

TERMINAL_OUTCOMES: Final[tuple[str, ...]] = (
    "success",
    "failure",
    "timeout",
    "cancellation",
    "unknown",
)


class FieldAuthorityState(StrEnum):
    """Closed states for a single producer-field authority claim."""

    AUTHORITATIVE = "authoritative"
    ABSENT = "absent"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class RequestFieldClassification:
    """One privacy-safe classification; it never carries a source value."""

    producer: str
    surface: str
    field: str
    state: FieldAuthorityState

    def __post_init__(self) -> None:
        if (self.producer, self.surface) not in PRODUCER_SURFACES:
            raise ValueError("classification producer and surface must be canonical")
        if self.field not in REQUIRED_REQUEST_FIELDS:
            raise ValueError("classification field is not required")


def current_classifications() -> tuple[RequestFieldClassification, ...]:
    """Return the current bounded authority matrix without inferred request identity."""
    authoritative = {"producer", "surface", "native_session_id", "project_id"}
    return tuple(
        RequestFieldClassification(
            producer=producer,
            surface=surface,
            field=field,
            state=(
                FieldAuthorityState.AUTHORITATIVE
                if field in authoritative
                else FieldAuthorityState.ABSENT
            ),
        )
        for producer, surface in PRODUCER_SURFACES
        for field in REQUIRED_REQUEST_FIELDS
    )


def validate_classifications(
    classifications: Iterable[RequestFieldClassification],
) -> tuple[RequestFieldClassification, ...]:
    """Require one, and only one, classified state for every matrix cell."""
    rows = tuple(classifications)
    expected = {
        (producer, surface, field)
        for producer, surface in PRODUCER_SURFACES
        for field in REQUIRED_REQUEST_FIELDS
    }
    seen: dict[tuple[str, str, str], FieldAuthorityState] = {}
    for row in rows:
        key = (row.producer, row.surface, row.field)
        if key in seen:
            raise ValueError("classification state conflicts or is duplicated")
        seen[key] = row.state
    if set(seen) != expected:
        raise ValueError("classification matrix omits or adds a producer-field cell")
    return rows


def build_proof(
    run_id: str,
    classifications: Iterable[RequestFieldClassification],
    *,
    source_boundary: str = "durable-schema-and-installed-authority",
    provenance: EvidenceProvenance = EvidenceProvenance.RETAINED,
) -> RequestExperimentProof:
    """Build E1's blocked proof from complete, privacy-safe classifications."""
    rows = validate_classifications(classifications)
    counts = dict.fromkeys(FieldAuthorityState, 0)
    for row in rows:
        counts[row.state] += 1
    blocked = tuple(
        field
        for field in REQUIRED_REQUEST_FIELDS
        if any(
            row.field == field and row.state is not FieldAuthorityState.AUTHORITATIVE
            for row in rows
        )
    )
    return RequestExperimentProof(
        experiment_id=RequestExperimentId.FIELD_AUDIT,
        run_id=run_id,
        result=ExperimentResult.BLOCKED,
        provenance=provenance,
        source_boundary=source_boundary,
        metrics={
            "classification_population": len(rows),
            "authoritative_fields": counts[FieldAuthorityState.AUTHORITATIVE],
            "absent_fields": counts[FieldAuthorityState.ABSENT],
            "ambiguous_fields": counts[FieldAuthorityState.AMBIGUOUS],
            "unsupported_fields": counts[FieldAuthorityState.UNSUPPORTED],
        },
        assertions={
            "complete_three_producer_matrix": (
                len(rows) == len(PRODUCER_SURFACES) * len(REQUIRED_REQUEST_FIELDS)
            ),
            "request_identity_not_inferred": all(
                row.state is not FieldAuthorityState.AUTHORITATIVE
                for row in rows
                if row.field in {"logical_request_id", "attempt_id"}
            ),
            "session_project_boundary_preserved": all(
                row.state is FieldAuthorityState.AUTHORITATIVE
                for row in rows
                if row.field in {"producer", "surface", "native_session_id", "project_id"}
            ),
        },
        evidence_ids=(),
        blocked_boundaries=blocked,
        proposal="authoritative producer request and attempt lifecycle fields",
    )


def classification_counts(
    classifications: Iterable[RequestFieldClassification],
) -> Mapping[FieldAuthorityState, int]:
    """Return conservation counts after enforcing the complete matrix."""
    rows = validate_classifications(classifications)
    return {state: sum(row.state is state for row in rows) for state in FieldAuthorityState}

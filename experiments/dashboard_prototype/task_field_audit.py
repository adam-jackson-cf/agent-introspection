"""Privacy-safe E-Task-1 task-operation field-authority classifications."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from experiments.dashboard_prototype.contracts import EvidenceProvenance, ExperimentResult
from experiments.dashboard_prototype.task_common import TaskExperimentId, TaskExperimentProof

PRODUCER_SURFACES: Final[tuple[tuple[str, str], ...]] = (
    ("omp", "omp"),
    ("codex-cli", "codex-cli"),
    ("codex-app-server", "codex-app-server"),
)
REQUIRED_TASK_FIELDS: Final[tuple[str, ...]] = (
    "producer",
    "surface",
    "native_session_id",
    "canonical_task_id",
    "source_session_relationship",
    "immutable_call_event_id",
    "source_order",
    "source_timestamp",
    "normalized_tool",
    "normalized_operation",
    "redacted_target",
    "failure_fingerprint",
    "explicit_tool_outcome",
    "sandbox_outcome",
    "mutation_relationship",
    "quality_command_relationship",
    "explicit_user_friction",
    "terminal_task_outcome",
    "terminal_timestamp",
    "project_identity_state",
)


class FieldAuthorityState(StrEnum):
    """Closed authority states for a producer-field matrix cell."""

    AUTHORITATIVE = "authoritative"
    PARTIAL = "partial"
    ABSENT = "absent"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class TaskFieldClassification:
    """A single privacy-safe classification; source values are never retained."""

    producer: str
    surface: str
    field: str
    state: FieldAuthorityState

    def __post_init__(self) -> None:
        if (self.producer, self.surface) not in PRODUCER_SURFACES:
            raise ValueError("classification producer and surface must be canonical")
        if self.field not in REQUIRED_TASK_FIELDS:
            raise ValueError("classification field is not required")


def current_classifications() -> tuple[TaskFieldClassification, ...]:
    """Return the retained matrix without inventing task or per-call authority."""
    authoritative = {
        "producer",
        "surface",
        "native_session_id",
        "source_session_relationship",
        "source_timestamp",
        "normalized_operation",
        "project_identity_state",
    }
    partial = {"source_order", "normalized_tool", "redacted_target", "failure_fingerprint"}
    return tuple(
        TaskFieldClassification(
            producer=producer,
            surface=surface,
            field=field,
            state=(
                FieldAuthorityState.AUTHORITATIVE
                if field in authoritative
                else FieldAuthorityState.PARTIAL
                if field in partial
                else FieldAuthorityState.ABSENT
            ),
        )
        for producer, surface in PRODUCER_SURFACES
        for field in REQUIRED_TASK_FIELDS
    )


def validate_classifications(
    classifications: Iterable[TaskFieldClassification],
) -> tuple[TaskFieldClassification, ...]:
    """Require one, and only one, privacy-safe state for every matrix cell."""
    rows = tuple(classifications)
    expected = {
        (producer, surface, field)
        for producer, surface in PRODUCER_SURFACES
        for field in REQUIRED_TASK_FIELDS
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
    classifications: Iterable[TaskFieldClassification],
    *,
    source_boundary: str = "durable-canonical-activity-and-latest-attribution-schema",
    provenance: EvidenceProvenance = EvidenceProvenance.RETAINED,
) -> TaskExperimentProof:
    """Build E1's typed blocked proof from the complete matrix."""
    rows = validate_classifications(classifications)
    counts = classification_counts(rows)
    blocked = tuple(
        field
        for field in REQUIRED_TASK_FIELDS
        if any(
            row.field == field and row.state is not FieldAuthorityState.AUTHORITATIVE
            for row in rows
        )
    )
    return TaskExperimentProof(
        experiment_id=TaskExperimentId.FIELD_AUDIT,
        run_id=run_id,
        result=ExperimentResult.BLOCKED,
        provenance=provenance,
        source_boundary=source_boundary,
        metrics={
            "classification_population": len(rows),
            **{f"{state.value}_fields": count for state, count in counts.items()},
        },
        assertions={
            "complete_three_producer_matrix": len(rows)
            == len(PRODUCER_SURFACES) * len(REQUIRED_TASK_FIELDS),
            "task_identity_not_inferred": all(
                row.state is not FieldAuthorityState.AUTHORITATIVE
                for row in rows
                if row.field == "canonical_task_id"
            ),
            "call_identity_not_inferred": all(
                row.state is not FieldAuthorityState.AUTHORITATIVE
                for row in rows
                if row.field == "immutable_call_event_id"
            ),
            "terminal_outcome_not_inferred": all(
                row.state is not FieldAuthorityState.AUTHORITATIVE
                for row in rows
                if row.field in {"explicit_tool_outcome", "terminal_task_outcome"}
            ),
        },
        evidence_ids=(),
        blocked_boundaries=blocked,
        proposal="authoritative task-operation lifecycle fields",
    )


def classification_counts(
    classifications: Iterable[TaskFieldClassification],
) -> Mapping[FieldAuthorityState, int]:
    """Return conservation counts after enforcing the complete matrix."""
    rows = validate_classifications(classifications)
    return {state: sum(row.state is state for row in rows) for state in FieldAuthorityState}

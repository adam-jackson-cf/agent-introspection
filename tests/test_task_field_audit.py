from dataclasses import replace

import pytest

from experiments.dashboard_prototype.contracts import ExperimentResult
from experiments.dashboard_prototype.task_field_audit import (
    PRODUCER_SURFACES,
    REQUIRED_TASK_FIELDS,
    FieldAuthorityState,
    TaskFieldClassification,
    build_proof,
    current_classifications,
    validate_classifications,
)


def test_current_matrix_is_the_exact_three_by_twenty_task_plan_matrix() -> None:
    rows = validate_classifications(current_classifications())
    assert len(rows) == 3 * 20
    assert len(rows) == len(PRODUCER_SURFACES) * len(REQUIRED_TASK_FIELDS)
    assert REQUIRED_TASK_FIELDS == (
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


def test_matrix_never_infers_task_call_or_terminal_outcome() -> None:
    rows = current_classifications()
    assert all(
        row.state is not FieldAuthorityState.AUTHORITATIVE
        for row in rows
        if row.field in {"canonical_task_id", "immutable_call_event_id", "terminal_task_outcome"}
    )
    proof = build_proof("task-field-audit", rows)
    assert proof.result is ExperimentResult.BLOCKED
    assert proof.assertions["task_identity_not_inferred"]
    assert proof.assertions["call_identity_not_inferred"]
    assert proof.assertions["terminal_outcome_not_inferred"]


def test_omitted_or_conflicting_matrix_cell_is_rejected() -> None:
    rows = current_classifications()
    with pytest.raises(ValueError, match="omits"):
        validate_classifications(rows[1:])
    with pytest.raises(ValueError, match="conflicts"):
        validate_classifications((replace(rows[0], state=FieldAuthorityState.PARTIAL), *rows))


def test_noncanonical_producer_surface_is_rejected() -> None:
    with pytest.raises(ValueError, match="canonical"):
        TaskFieldClassification(
            "codex-cli", "codex-app-server", "producer", FieldAuthorityState.AUTHORITATIVE
        )

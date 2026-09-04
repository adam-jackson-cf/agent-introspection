from dataclasses import replace

import pytest

from experiments.dashboard_prototype.request_field_audit import (
    PRODUCER_SURFACES,
    REQUIRED_REQUEST_FIELDS,
    TERMINAL_OUTCOMES,
    FieldAuthorityState,
    RequestFieldClassification,
    current_classifications,
    validate_classifications,
)


def test_current_matrix_classifies_every_field_for_three_producers() -> None:
    rows = validate_classifications(current_classifications())
    assert len(rows) == len(PRODUCER_SURFACES) * len(REQUIRED_REQUEST_FIELDS)
    assert all(
        row.state is FieldAuthorityState.AUTHORITATIVE
        for row in rows
        if row.field in {"producer", "surface", "native_session_id", "project_id"}
    )
    assert all(
        row.state is FieldAuthorityState.ABSENT
        for row in rows
        if row.field in {"logical_request_id", "attempt_id"}
    )
    assert set(REQUIRED_REQUEST_FIELDS) == {
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
    }
    assert TERMINAL_OUTCOMES == (
        "success",
        "failure",
        "timeout",
        "cancellation",
        "unknown",
    )


def test_omitted_field_is_rejected() -> None:
    rows = current_classifications()
    with pytest.raises(ValueError, match="omits"):
        validate_classifications(rows[1:])


def test_conflicting_state_is_rejected() -> None:
    rows = current_classifications()
    with pytest.raises(ValueError, match="conflicts"):
        validate_classifications((replace(rows[0], state=FieldAuthorityState.AMBIGUOUS), *rows))


def test_producer_surface_namespace_collision_is_rejected() -> None:
    with pytest.raises(ValueError, match="canonical"):
        RequestFieldClassification(
            "codex-cli", "codex-app-server", "producer", FieldAuthorityState.AUTHORITATIVE
        )

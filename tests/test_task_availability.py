import pytest

from experiments.dashboard_prototype.contracts import EvidenceProvenance
from experiments.dashboard_prototype.task_availability import (
    SUPPORTED_SURFACES,
    TASK_MEASURES,
    TaskAvailabilityClassification,
    TaskRouteState,
    build_proof,
    current_classifications,
    validate_classifications,
)


def test_closed_matrix_is_complete_and_retained_proof_fails_closed() -> None:
    rows = current_classifications()
    proof = build_proof("task-availability", rows)

    assert len(rows) == len(SUPPORTED_SURFACES) * len(TASK_MEASURES)
    assert proof.result.value == "Blocked"
    assert proof.metrics["route_manifest_count"] == len(rows)
    assert proof.assertions["redaction_boundary_preserved"]


def test_retained_all_authoritative_matrix_is_not_fresh_authority() -> None:
    rows = tuple(
        TaskAvailabilityClassification(
            row.producer, row.surface, row.measure, TaskRouteState.AUTHORITATIVE
        )
        for row in current_classifications()
    )
    proof = build_proof("retained-authority", rows, provenance=EvidenceProvenance.RETAINED)

    assert proof.result.value == "Blocked"
    assert not proof.assertions["every_route_fresh_authoritative"]


def test_omitted_or_duplicate_route_is_rejected() -> None:
    rows = current_classifications()
    with pytest.raises(ValueError, match="omits or adds"):
        validate_classifications(rows[1:])
    with pytest.raises(ValueError, match="conflicts or is duplicated"):
        validate_classifications((*rows, rows[0]))


def test_wrong_surface_is_rejected() -> None:
    with pytest.raises(ValueError, match="producer and surface"):
        TaskAvailabilityClassification("codex-cli", "omp", "M2", TaskRouteState.AUTHORITATIVE)


def test_fresh_all_authoritative_matrix_stays_runner_blocked() -> None:
    rows = tuple(
        TaskAvailabilityClassification(
            row.producer, row.surface, row.measure, TaskRouteState.AUTHORITATIVE
        )
        for row in current_classifications()
    )
    proof = build_proof("forged", rows, provenance=EvidenceProvenance.FRESH_REAL)

    assert proof.result.value == "Blocked"
    assert proof.assertions["every_route_fresh_authoritative"]
    assert not proof.assertions["remote_calculation_reconciled"]
    assert proof.evidence_ids == ()
    assert proof.blocked_boundaries == ("runner_owned_remote_reconciliation",)

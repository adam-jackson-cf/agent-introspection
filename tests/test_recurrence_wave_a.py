from __future__ import annotations

from datetime import UTC, datetime, timedelta

from experiments.dashboard_prototype.contracts import EvidenceProvenance, ExperimentResult
from experiments.dashboard_prototype.recurrence_wave_a import (
    M13Availability,
    M13TaskIdentityState,
    RecurrenceOccurrence,
    RecurrenceProofBoundary,
    build_proof,
    reduce_m16_project_concentration,
)

START = datetime(2026, 9, 2, 12, tzinfo=UTC)
END = START + timedelta(minutes=5)


def _fingerprint(kind: str, digit: str) -> str:
    return f"{kind}:{digit * 64}"


def test_m16_breaks_top_project_ties_deterministically_and_conserves_population() -> None:
    reductions = reduce_m16_project_concentration(
        (
            RecurrenceOccurrence(
                "detector", _fingerprint("activity", "a"), _fingerprint("project", "b")
            ),
            RecurrenceOccurrence(
                "detector", _fingerprint("activity", "a"), _fingerprint("project", "c")
            ),
            RecurrenceOccurrence("detector", _fingerprint("activity", "a"), None),
        )
    )

    assert reductions[0].top_project_id == _fingerprint("project", "b")
    assert reductions[0].top_project_percentage == 50
    assert (
        reductions[0].total_count == reductions[0].attributed_count + reductions[0].unresolved_count
    )


def test_m16_excludes_unresolved_and_omits_percentage_with_zero_attributed_denominator() -> None:
    reductions = reduce_m16_project_concentration(
        (RecurrenceOccurrence("detector", _fingerprint("activity", "a"), None),)
    )

    assert reductions[0].attributed_count == 0
    assert reductions[0].unresolved_count == 1
    assert reductions[0].top_project_id is None
    assert reductions[0].top_project_percentage is None


def test_e0_proof_withholds_task_recurrence_for_missing_durable_identity() -> None:
    proof = build_proof(
        boundary=RecurrenceProofBoundary(
            run_id="run-1",
            source_start=START,
            source_end=END,
            source_boundary="source-ended-at",
        ),
        m16=(),
        m13=(M13Availability("omp", "omp", M13TaskIdentityState.ABSENT, 1),),
        provenance=EvidenceProvenance.SYNTHETIC,
    )

    assert proof.result is ExperimentResult.BLOCKED
    assert proof.assertions["m13_durable_task_identity_available"] is False
    assert proof.blocked_boundaries == ("missing_durable_authority",)

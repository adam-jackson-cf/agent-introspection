from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from experiments.dashboard_prototype.contracts import (
    EvidenceProvenance,
    ExperimentResult,
    PrototypeContractError,
)
from experiments.dashboard_prototype.pipeline_integrity import (
    IntegrityInvariant,
    IntegrityObservation,
    build_integrity_proof,
    reduce_integrity_incidents,
)

START = datetime(2026, 9, 1, 12, tzinfo=UTC)
END = START + timedelta(hours=1)


def _row(
    invariant: IntegrityInvariant,
    *,
    cohort: str = "cohort-ok",
    offset_seconds: int = 1,
    affected: str = "subject-01",
) -> IntegrityObservation:
    return IntegrityObservation(
        invariant=invariant,
        event_or_projection="terminal-projection",
        source_time=START + timedelta(seconds=offset_seconds),
        producer="codex-cli",
        runtime="native",
        cohort_short_identity=cohort,
        affected_short_identity=affected,
    )


def test_projects_every_incident_class_in_deterministic_order() -> None:
    rows = [
        _row(invariant, cohort=f"cohort-{index}", offset_seconds=5 - index)
        for index, invariant in enumerate(IntegrityInvariant)
    ]

    first = reduce_integrity_incidents(rows, start=START, end=END)
    second = reduce_integrity_incidents(list(reversed(rows)), start=START, end=END)

    assert first.incidents == second.incidents
    assert [incident.invariant for incident in first.incidents] == [
        IntegrityInvariant.CONFLICTING_IDENTITY,
        IntegrityInvariant.VERSION_GAP,
        IntegrityInvariant.DETERMINISTIC_ID_CONFLICT,
        IntegrityInvariant.CANONICAL_LIFECYCLE_CONTEXT_REJECTION,
        IntegrityInvariant.DURABLE_INTEGRITY_FAILURE,
    ]
    assert all(incident.incident_id.startswith("p11-") for incident in first.incidents)
    assert len({incident.incident_id for incident in first.incidents}) == len(first.incidents)


def test_rejects_full_ids_and_unsafe_source_text() -> None:
    with pytest.raises(PrototypeContractError, match="redacted"):
        _row(
            IntegrityInvariant.CONFLICTING_IDENTITY,
            affected="01234567-89ab-cdef-0123-456789abcdef",
        )
    with pytest.raises(PrototypeContractError, match="redacted"):
        _row(IntegrityInvariant.VERSION_GAP, cohort="/private/session")
    with pytest.raises(PrototypeContractError, match="redacted"):
        _row(IntegrityInvariant.DURABLE_INTEGRITY_FAILURE, affected="sk-secret-token")


def test_duplicate_rows_conserve_population_without_duplicate_incidents() -> None:
    row = _row(IntegrityInvariant.CANONICAL_LIFECYCLE_CONTEXT_REJECTION)
    reduction = reduce_integrity_incidents([row, row], start=START, end=END)

    assert reduction.selected_row_count == 2
    assert reduction.duplicate_row_count == 1
    assert len(reduction.incidents) == 1


def test_corrupt_or_conflicting_cohorts_are_never_partially_aggregated() -> None:
    reduction = reduce_integrity_incidents(
        [
            _row(IntegrityInvariant.CONFLICTING_IDENTITY, cohort="cohort-bad"),
            _row(
                IntegrityInvariant.CANONICAL_LIFECYCLE_CONTEXT_REJECTION,
                cohort="cohort-bad",
                offset_seconds=2,
            ),
            _row(
                IntegrityInvariant.CANONICAL_LIFECYCLE_CONTEXT_REJECTION,
                cohort="cohort-good",
                offset_seconds=3,
            ),
        ],
        start=START,
        end=END,
    )

    assert reduction.withheld_cohorts == ("cohort-bad",)
    assert reduction.aggregate_counts == {
        "p11.canonical-lifecycle-context-rejection.codex-cli.native": 1
    }
    assert len(reduction.incidents) == 3


def test_selected_range_excludes_start_and_includes_end() -> None:
    reduction = reduce_integrity_incidents(
        [
            _row(IntegrityInvariant.VERSION_GAP, offset_seconds=0),
            _row(IntegrityInvariant.VERSION_GAP, offset_seconds=3600, affected="subject-02"),
        ],
        start=START,
        end=END,
    )

    assert reduction.selected_row_count == 1
    assert reduction.incidents[0].source_time == END


def test_proof_blocks_missing_boundaries_and_fails_falsified_or_unsafe_metrics() -> None:
    clean = reduce_integrity_incidents([], start=START, end=END)
    blocked = build_integrity_proof(
        clean,
        run_id=None,
        provenance=None,
        source_boundary=None,
        expected_remote_counts=None,
    )
    failed = build_integrity_proof(
        clean,
        run_id="run-4",
        provenance=EvidenceProvenance.FRESH_REAL,
        source_boundary="source_time > start AND source_time <= end",
        expected_remote_counts={"p11.unexpected": 1},
    )
    unsafe = replace(clean, aggregate_counts={"unsafe/path": 1})
    unsafe_proof = build_integrity_proof(
        unsafe,
        run_id="run-4",
        provenance=EvidenceProvenance.FRESH_REAL,
        source_boundary="source_time > start AND source_time <= end",
        expected_remote_counts={"unsafe/path": 1},
    )

    assert blocked.result is ExperimentResult.BLOCKED
    assert blocked.blocked_boundaries == (
        "exact run_id",
        "exact provenance",
        "exact source boundary",
        "bounded remote calculation counts",
    )
    assert failed.result is ExperimentResult.FAILED
    assert unsafe_proof.assertions["safe_remote_metrics"] is False
    assert unsafe_proof.result is ExperimentResult.FAILED


def test_clean_fresh_reconciliation_is_proven() -> None:
    proof = build_integrity_proof(
        reduce_integrity_incidents([], start=START, end=END),
        run_id="run-4",
        provenance=EvidenceProvenance.FRESH_REAL,
        source_boundary="source_time > start AND source_time <= end",
        expected_remote_counts={},
    )

    assert proof.result is ExperimentResult.PROVEN

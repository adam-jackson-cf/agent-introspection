from __future__ import annotations

from datetime import UTC, datetime, timedelta

from experiments.dashboard_prototype.attribution_baseline import (
    AttributionBaselineProofInput,
    AttributionRowEvidenceInput,
    CanonicalActivityVersion,
    CapabilityState,
    LifecycleSessionObservation,
    ProducerAuthority,
    SourceSessionObservation,
    build_attribution_baseline_proof,
    build_row_evidence_inputs,
    reduce_attribution_baseline,
)
from experiments.dashboard_prototype.contracts import EvidenceProvenance, ExperimentResult


def _authority(producer: str) -> ProducerAuthority:
    return ProducerAuthority(
        producer,
        producer,
        f"proof-{producer}",
        True,
        "a" * 64,
        "project",
        "git",
        {
            name: CapabilityState.PASSED
            if name != "workspace_change"
            else CapabilityState.NOT_EXPOSED
            for name in (
                "fresh",
                "resume",
                "end",
                "concurrent_projects",
                "non_git",
                "workspace_change",
            )
        },
    )


def test_latest_versions_conserve_common_p7_p8_population() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    activities = (
        CanonicalActivityVersion(
            "one",
            1,
            start + timedelta(seconds=1),
            "omp",
            "omp",
            "session-a",
            "unresolved",
            None,
            "missing_context",
        ),
        CanonicalActivityVersion(
            "one",
            2,
            start + timedelta(seconds=1),
            "omp",
            "omp",
            "session-a",
            "attributed",
            "b" * 64,
            None,
        ),
        CanonicalActivityVersion(
            "two",
            1,
            start + timedelta(seconds=3),
            "codex-cli",
            "codex-cli",
            "session-b",
            "unresolved",
            None,
            "no_authoritative_context",
        ),
    )
    reduced = reduce_attribution_baseline(
        (_authority("omp"), _authority("codex-cli")),
        activities,
        start=start,
        end=start + timedelta(minutes=1),
    )
    assert reduced.p7.eligible == 2
    assert (reduced.p7.attributed, reduced.p7.unresolved, reduced.p7.distinct_projects) == (1, 1, 1)
    assert reduced.p7.conserves
    assert reduced.p8 == {"no_authoritative_context": 1}


def test_missing_current_activity_authority_blocks_p7_p8_not_retained_p5() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    proof = build_attribution_baseline_proof(
        run_id="baseline-1",
        provenance=EvidenceProvenance.FRESH_REAL,
        source_boundary="bounded",
        proof_input=AttributionBaselineProofInput(
            authorities=(_authority("omp"), _authority("codex-cli")),
            activities=None,
            start=start,
            sources=None,
            lifecycles=None,
            end=start + timedelta(minutes=1),
        ),
    )
    assert proof.result is ExperimentResult.BLOCKED
    assert proof.blocked_boundaries == ("current_source_membership",)
    assert proof.metrics["p5.omp.omp.fresh"] == "passed"


def test_row_inputs_bind_each_obligation_to_exact_session_event_identities() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    activity = CanonicalActivityVersion(
        "one",
        1,
        start + timedelta(seconds=1),
        "omp",
        "omp",
        "session-a",
        "unresolved",
        None,
        "no_authoritative_context",
    )

    rows = build_row_evidence_inputs(
        (activity,),
        (SourceSessionObservation("omp", "omp", "session-a", activity.source_time),),
        (LifecycleSessionObservation("omp", "omp", "session-a", start, None),),
        start=start,
        end=start + timedelta(minutes=1),
    )

    assert {(row.row_id, row.producer) for row in rows} == {
        ("A07", "omp"),
        ("A08", "omp"),
        ("A09", "omp"),
    }
    for row in rows:
        assert isinstance(row, AttributionRowEvidenceInput)
        row.validate()
        assert set(row.event_ids) == {"source", "reducer", "delivery"}
        assert row.direct_result == row.oracle_result
        assert row.direct_result_types == row.oracle_result_types


def test_missing_source_authority_omits_only_its_a07_row_candidate() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    activity = CanonicalActivityVersion(
        "one",
        1,
        start + timedelta(seconds=1),
        "codex-cli",
        "codex-cli",
        "session-b",
        "attributed",
        "b" * 64,
        None,
    )
    rows = build_row_evidence_inputs(
        (activity,), None, None, start=start, end=start + timedelta(minutes=1)
    )
    assert {(row.row_id, row.producer) for row in rows} == {
        ("A08", "codex-cli"),
        ("A09", "codex-cli"),
    }

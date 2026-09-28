from __future__ import annotations

from datetime import UTC, datetime, timedelta

from experiments.dashboard_prototype.attribution_baseline import (
    AttributionBaselineProofInput,
    CanonicalActivityVersion,
    CapabilityState,
    ProducerAuthority,
    build_attribution_baseline_proof,
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
    start_ns = int(start.timestamp()) * 1_000_000_000
    end_ns = start_ns + 60_000_000_000
    activities = (
        CanonicalActivityVersion(
            "one",
            1,
            start_ns + 1,
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
            start_ns + 1,
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
            end_ns,
            "codex-cli",
            "codex-cli",
            "session-b",
            "unresolved",
            None,
            "no_authoritative_context",
        ),
        CanonicalActivityVersion(
            "at-start",
            1,
            start_ns,
            "omp",
            "omp",
            "session-a",
            "attributed",
            "c" * 64,
            None,
        ),
        CanonicalActivityVersion(
            "after-end",
            1,
            end_ns + 1,
            "omp",
            "omp",
            "session-a",
            "attributed",
            "d" * 64,
            None,
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

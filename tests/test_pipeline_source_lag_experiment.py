from datetime import UTC, datetime, timedelta

import pytest

from experiments.dashboard_prototype.contracts import (
    EvidenceProvenance,
    ExperimentResult,
    PrototypeContractError,
)
from experiments.dashboard_prototype.pipeline_source_lag import (
    AuthoritativeSourceObservation,
    SourceLagCohort,
    SourceLagDisposition,
    SourceLagScan,
    build_source_lag_proof,
    reduce_source_lag,
)

BASE = datetime(2026, 9, 1, tzinfo=UTC)
COHORT = SourceLagCohort("omp", "terminal", "session-end")
OTHER_COHORT = SourceLagCohort("codex-cli", "terminal", "session-end")


def _scan(
    scan_id: str,
    bound_seconds: int,
    completed_seconds: int,
    *,
    cohort: SourceLagCohort = COHORT,
    capability_available: bool | None = True,
) -> SourceLagScan:
    return SourceLagScan(
        scan_id=scan_id,
        cohort=cohort,
        started_at=BASE + timedelta(seconds=min(1, completed_seconds)),
        population_start=BASE,
        population_end=BASE + timedelta(seconds=100),
        completed_at=BASE + timedelta(seconds=completed_seconds),
        extraction_bound=BASE + timedelta(seconds=bound_seconds),
        capability_available=capability_available,
    )


def _observation(  # noqa: PLR0913
    observation_id: str,
    source_seconds: int,
    identity: str,
    *,
    cohort: SourceLagCohort = COHORT,
    scan_id: str = "shared",
    extraction_bound_seconds: int | None = None,
) -> AuthoritativeSourceObservation:
    return AuthoritativeSourceObservation(
        observation_id=observation_id,
        scan_id=scan_id,
        cohort=cohort,
        source_time=BASE + timedelta(seconds=source_seconds),
        current_identity=identity,
        extraction_bound=(
            BASE + timedelta(seconds=extraction_bound_seconds)
            if extraction_bound_seconds is not None
            else None
        ),
    )


def test_reducer_selects_per_bound_and_computes_percentiles() -> None:
    reduction = reduce_source_lag(
        scans=(_scan("first", 20, 20), _scan("second", 50, 50), _scan("third", 100, 100)),
        observations=(
            _observation("old", 10, "identity-old", scan_id="first", extraction_bound_seconds=20),
            _observation(
                "middle", 30, "identity-middle", scan_id="second", extraction_bound_seconds=50
            ),
            _observation(
                "current", 80, "identity-current", scan_id="third", extraction_bound_seconds=100
            ),
        ),
        normative_cohorts=(COHORT,),
    )

    assert [(row.scan_id, row.selected_observation_id) for row in reduction.selections] == [
        ("first", "old"),
        ("second", "middle"),
        ("third", "current"),
    ]
    assert reduction.selections[-1].selected_current_identity == "identity-current"
    assert reduction.selections[-1].negative_skew is False
    metric = reduction.cohorts[0]
    assert (metric.n, metric.p50_lag_seconds, metric.p95_lag_seconds) == (3, 20.0, 20.0)
    assert metric.negative_skew_count == 0


def test_negative_skew_remains_accepted_but_excludes_latency_percentiles() -> None:
    reduction = reduce_source_lag(
        scans=(_scan("positive", 20, 20), _scan("skewed", 30, 30)),
        observations=(
            _observation(
                "positive", 10, "positive-current", scan_id="positive", extraction_bound_seconds=20
            ),
            _observation(
                "skewed", 31, "skewed-current", scan_id="skewed", extraction_bound_seconds=30
            ),
        ),
        normative_cohorts=(COHORT,),
    )

    metric = reduction.cohorts[0]
    assert (metric.accepted, metric.n, metric.p50_lag_seconds, metric.p95_lag_seconds) == (
        2,
        1,
        10.0,
        10.0,
    )
    assert metric.negative_skew_count == 1
    proof = build_source_lag_proof(
        "mixed", EvidenceProvenance.FRESH_REAL, "bounded scan", reduction
    )
    assert proof.metrics["n"] == 1
    assert proof.metrics["negative_skew_count"] == 1


def test_reducer_accepts_negative_skew_only_from_its_exact_scan_membership() -> None:
    excluded_start = _scan("excluded-start", 0, 0)
    included_end = _scan("included-end", 10, 100)
    future_only = _scan("future-only", 10, 10)
    duplicate = _scan("included-end", 10, 100)
    reduction = reduce_source_lag(
        scans=(excluded_start, included_end, future_only, duplicate),
        observations=(
            _observation(
                "future",
                11,
                "future-identity",
                scan_id="future-only",
                extraction_bound_seconds=10,
            ),
        ),
        normative_cohorts=(COHORT,),
    )

    assert [row.scan_id for row in reduction.selections] == [
        "future-only",
        "included-end",
        "included-end",
    ]
    assert [row.disposition for row in reduction.selections] == [
        SourceLagDisposition.ACCEPTED,
        SourceLagDisposition.REJECTED,
        SourceLagDisposition.DUPLICATE,
    ]
    assert reduction.selections[0].lag_seconds == -1.0
    assert reduction.selections[0].negative_skew is True
    metric = reduction.cohorts[0]
    assert (
        metric.accepted,
        metric.missing_capability,
        metric.rejected,
        metric.duplicate,
        metric.negative_skew_count,
    ) == (1, 0, 1, 1, 1)
    assert metric.population == len(reduction.selections)
    assert reduction.blocked_boundaries == ("source-observation:omp/terminal/session-end:count=1",)


def test_capability_absence_is_not_applicable_and_missing_capability_is_blocked() -> None:
    absent = reduce_source_lag(
        scans=(_scan("absent", 5, 5, capability_available=False),),
        observations=(),
        normative_cohorts=(COHORT,),
    )
    absent_proof = build_source_lag_proof(
        "run-1", EvidenceProvenance.RETAINED, "bounded scan", absent
    )
    assert absent_proof.result is ExperimentResult.NOT_APPLICABLE
    assert absent.cohorts[0].missing_capability == 1

    missing = reduce_source_lag(
        scans=(_scan("unknown", 5, 5, capability_available=None),),
        observations=(),
        normative_cohorts=(COHORT,),
    )
    proof = build_source_lag_proof("run-2", EvidenceProvenance.RETAINED, "bounded scan", missing)
    assert proof.result is ExperimentResult.BLOCKED
    assert proof.blocked_boundaries == ("capability:omp/terminal/session-end:count=1",)


def test_reducer_preserves_shared_scan_across_cohorts_and_is_order_invariant() -> None:
    scans = (
        _scan("shared", 20, 20),
        _scan("shared", 20, 20, cohort=OTHER_COHORT),
        _scan("shared", 20, 20),
    )
    observations = (
        _observation("a-current", 15, "a", scan_id="shared", extraction_bound_seconds=20),
        _observation(
            "b-current",
            15,
            "b",
            cohort=OTHER_COHORT,
            scan_id="shared",
            extraction_bound_seconds=20,
        ),
    )

    forward = reduce_source_lag(scans, observations, (COHORT, OTHER_COHORT))
    backward = reduce_source_lag(reversed(scans), reversed(observations), (OTHER_COHORT, COHORT))

    assert forward == backward
    assert [(row.cohort, row.disposition) for row in forward.selections] == [
        (OTHER_COHORT, SourceLagDisposition.ACCEPTED),
        (COHORT, SourceLagDisposition.ACCEPTED),
        (COHORT, SourceLagDisposition.DUPLICATE),
    ]
    assert [(metric.cohort, metric.population) for metric in forward.cohorts] == [
        (OTHER_COHORT, 1),
        (COHORT, 2),
    ]


def test_scan_rejects_extraction_bound_after_completion() -> None:
    with pytest.raises(PrototypeContractError, match="extraction bound"):
        _scan("post-completion", 11, 10)


def test_conflicting_scan_duplicates_fail_closed() -> None:
    with pytest.raises(PrototypeContractError, match="conflicting source-lag scans"):
        reduce_source_lag(
            scans=(_scan("shared", 10, 10), _scan("shared", 9, 10)),
            observations=(),
            normative_cohorts=(COHORT,),
        )


def test_missing_cohort_across_scans_is_summarized_without_losing_count() -> None:
    reduction = reduce_source_lag(
        scans=(
            _scan("first", 10, 10),
            _scan("second", 20, 20),
            _scan("third", 30, 30),
        ),
        observations=(
            _observation("first", 10, "first", scan_id="first", extraction_bound_seconds=10),
            _observation("second", 20, "second", scan_id="second", extraction_bound_seconds=20),
            _observation("third", 30, "third", scan_id="third", extraction_bound_seconds=30),
        ),
        normative_cohorts=(COHORT, OTHER_COHORT),
    )
    proof = build_source_lag_proof(
        "run-3", EvidenceProvenance.FRESH_REAL, "bounded scan", reduction
    )

    assert [(row.cohort, row.disposition) for row in reduction.selections] == [
        (COHORT, SourceLagDisposition.ACCEPTED),
        (OTHER_COHORT, SourceLagDisposition.REJECTED),
        (COHORT, SourceLagDisposition.ACCEPTED),
        (OTHER_COHORT, SourceLagDisposition.REJECTED),
        (COHORT, SourceLagDisposition.ACCEPTED),
        (OTHER_COHORT, SourceLagDisposition.REJECTED),
    ]
    assert proof.result is ExperimentResult.BLOCKED
    assert proof.blocked_boundaries == ("scan-completion:codex-cli/terminal/session-end:count=3",)


def test_many_missing_observations_are_bounded_by_cohort_with_exact_count() -> None:
    scan_count = 464
    reduction = reduce_source_lag(
        scans=tuple(_scan(f"scan-{ordinal}", 50, 50) for ordinal in range(1, scan_count + 1)),
        observations=(),
        normative_cohorts=(COHORT,),
    )

    assert len(reduction.selections) == scan_count
    assert all(row.disposition is SourceLagDisposition.REJECTED for row in reduction.selections)
    assert reduction.blocked_boundaries == (
        "source-observation:omp/terminal/session-end:count=464",
    )


def test_unbound_or_mismatched_source_observations_are_rejected_per_scan() -> None:
    reduction = reduce_source_lag(
        scans=(_scan("bound", 20, 20),),
        observations=(
            _observation("unbound", 10, "unbound", scan_id="bound"),
            _observation(
                "mismatched",
                10,
                "mismatched",
                scan_id="bound",
                extraction_bound_seconds=19,
            ),
        ),
        normative_cohorts=(COHORT,),
    )

    assert reduction.selections[0].disposition is SourceLagDisposition.REJECTED
    assert reduction.blocked_boundaries == (
        "source-extraction-bound:omp/terminal/session-end:count=1",
    )


def test_multi_scan_cohort_conserves_population_and_exact_skew_count() -> None:
    reduction = reduce_source_lag(
        scans=(_scan("first", 10, 10), _scan("second", 20, 20)),
        observations=(
            _observation("first", 5, "first", scan_id="first", extraction_bound_seconds=10),
            _observation(
                "second",
                21,
                "second",
                scan_id="second",
                extraction_bound_seconds=20,
            ),
        ),
        normative_cohorts=(COHORT,),
    )

    metric = reduction.cohorts[0]
    assert metric.population == len(reduction.selections) == 2
    assert (metric.accepted, metric.n, metric.negative_skew_count) == (2, 1, 1)

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from experiments.dashboard_prototype.attribution_late_context import (
    ActivityVersion,
    build_late_context_proof,
    reduce_late_context,
)
from experiments.dashboard_prototype.attribution_lifecycle_delay import (
    AcceptedLifecycleInterval,
    AuthoritativeSourceEvent,
    LifecycleDelayCohort,
    reduce_lifecycle_delay,
)
from experiments.dashboard_prototype.contracts import EvidenceProvenance, ExperimentResult

START = datetime(2026, 9, 1, tzinfo=UTC)
END = START + timedelta(minutes=10)
COHORT = LifecycleDelayCohort("omp", "omp")


def _source(event_id: str, session: str, offset: int) -> AuthoritativeSourceEvent:
    return AuthoritativeSourceEvent(event_id, COHORT, session, START + timedelta(seconds=offset))


def _interval(event_id: str, session: str, start: int, end: int) -> AcceptedLifecycleInterval:
    return AcceptedLifecycleInterval(
        event_id, COHORT, session, START + timedelta(seconds=start), START + timedelta(seconds=end)
    )


def test_selects_first_strictly_after_start_with_timestamp_event_id_ties() -> None:
    reduction = reduce_lifecycle_delay(
        [
            _source("start", "a", 0),
            _source("z", "a", 10),
            _source("a", "a", 10),
            _source("end", "b", 600),
            _source("outside", "c", 601),
        ],
        [_interval("ia", "a", -5, 20), _interval("ib", "b", 590, 601)],
        [COHORT],
        START,
        END,
    )
    assert [(row.native_session_id, row.source_event_id) for row in reduction.selections] == [
        ("a", "a"),
        ("b", "end"),
    ]


def test_contains_half_open_interval_endpoints() -> None:
    reduction = reduce_lifecycle_delay(
        [_source("inside", "a", 5), _source("end", "b", 10)],
        [_interval("ia", "a", 0, 10), _interval("ib", "b", 0, 10)],
        [COHORT],
        START,
        END,
    )
    by_session = {row.native_session_id: row for row in reduction.selections}
    assert by_session["a"].matched
    assert not by_session["b"].matched
    assert reduction.blocked_boundaries == ("lifecycle-authority:omp/omp",)


def test_reduces_percentiles_per_cohort_from_matched_sessions() -> None:
    reduction = reduce_lifecycle_delay(
        [
            _source("one", "a", 1),
            _source("five", "b", 5),
            _source("nine", "c", 9),
        ],
        [
            _interval("ia", "a", 0, 20),
            _interval("ib", "b", 0, 20),
            _interval("ic", "c", 0, 20),
        ],
        [COHORT],
        START,
        END,
    )
    metrics = reduction.cohorts[0]
    assert (metrics.matched_sessions, metrics.n) == (3, 3)
    assert (metrics.p50_delay_seconds, metrics.p95_delay_seconds) == (5.0, 9.0)


def test_ambiguous_containing_intervals_block_broad_linkage() -> None:
    reduction = reduce_lifecycle_delay(
        [_source("source", "a", 5)],
        [_interval("one", "a", 0, 10), _interval("two", "a", 1, 9)],
        [COHORT],
        START,
        END,
    )
    selection = reduction.selections[0]
    assert not reduction.contradictory
    assert not selection.matched
    assert selection.interval_event_id is None
    assert reduction.blocked_boundaries == ("lifecycle-authority:omp/omp",)


def _version(version: int, state: str, event_id: str, *, activity_id: str) -> ActivityVersion:
    return ActivityVersion(
        activity_id=activity_id,
        version=version,
        event_id=event_id,
        producer="omp",
        surface="omp",
        source_time=START + timedelta(seconds=1),
        attribution_state=state,
        attribution_method="lifecycle",
        reason_code="missing_context" if state == "unresolved" else None,
        project_id="project" if state == "resolved" else None,
    )


def test_versions_conserve_ever_unresolved_denominator_and_block_missing_remote_history() -> None:
    reduction = reduce_late_context(
        (
            _version(1, "unresolved", "immediate-1", activity_id="immediate"),
            _version(2, "resolved", "immediate-2", activity_id="immediate"),
            _version(1, "unresolved", "superseded-1", activity_id="superseded"),
            _version(2, "resolved", "superseded-2", activity_id="superseded"),
            _version(3, "resolved", "superseded-3", activity_id="superseded"),
        ),
        start=START,
        end=END,
    )
    assert reduction.transition_count == 1
    assert reduction.ever_unresolved_count == 2
    assert reduction.invalid_activity_count == 0
    proof = build_late_context_proof(
        "run",
        EvidenceProvenance.FRESH_REAL,
        "source",
        reduction,
        remote_denominator=None,
    )
    assert proof.result is ExperimentResult.BLOCKED
    assert "late-context.remote-ever-unresolved-denominator" in proof.blocked_boundaries

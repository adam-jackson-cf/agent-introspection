from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from experiments.dashboard_prototype.contracts import (
    EvidenceProvenance,
    ExperimentResult,
    PrototypeContractError,
)
from experiments.dashboard_prototype.pipeline_outbox import (
    E5_REMOTE_SQL,
    DeliveryAttempt,
    DeliveryAttemptStatus,
    E5RemotePopulation,
    OutboxEvent,
    OutboxEvidenceBoundary,
    OutboxReduction,
    build_outbox_proof,
    e5_local_oracle,
    parse_e5_remote_result,
    reduce_outbox,
)

START = datetime(2026, 9, 1, 12, tzinfo=UTC)
END = START + timedelta(minutes=10)
COMPLETED = END + timedelta(seconds=30)


def _event(event_id: str, *, pending: bool, created_at: datetime | None = None) -> OutboxEvent:
    return OutboxEvent(
        event_id=event_id,
        created_at=created_at or START + timedelta(minutes=1),
        destination="signoz",
        event_type="telemetry",
        is_pending=pending,
    )


def _attempt(
    attempt_id: str,
    event_id: str,
    *,
    status: DeliveryAttemptStatus,
    at: datetime | None = None,
    drain_id: str = "drain-final",
) -> DeliveryAttempt:
    return DeliveryAttempt(
        attempt_id=attempt_id,
        event_id=event_id,
        drain_id=drain_id,
        attempted_at=at or START + timedelta(minutes=5),
        status=status,
        error_class="TimeoutError" if status is DeliveryAttemptStatus.FAILED else None,
    )


def _boundary(
    events: tuple[OutboxEvent, ...] | None,
    attempts: tuple[DeliveryAttempt, ...] | None,
) -> OutboxEvidenceBoundary:
    return OutboxEvidenceBoundary(
        events=events,
        attempts=attempts,
        final_drain_id="drain-final",
        source_start=START,
        source_end=END,
        scan_completed_at=COMPLETED,
    )


def _reduce(
    events: tuple[OutboxEvent, ...], attempts: tuple[DeliveryAttempt, ...]
) -> OutboxReduction:
    return reduce_outbox(boundary=_boundary(events, attempts))


def test_overlap_and_retries_preserve_snapshot_populations() -> None:
    reduction = _reduce(
        (
            _event("event-1", pending=False, created_at=START + timedelta(minutes=1)),
            _event("event-2", pending=False, created_at=START + timedelta(minutes=2)),
            _event("event-3", pending=False),
        ),
        (
            _attempt("attempt-1", "event-1", status=DeliveryAttemptStatus.FAILED),
            _attempt(
                "attempt-2",
                "event-1",
                status=DeliveryAttemptStatus.DELIVERED,
                at=START + timedelta(minutes=6),
            ),
            _attempt("attempt-3", "event-2", status=DeliveryAttemptStatus.DELIVERED),
            _attempt(
                "attempt-other-drain",
                "event-3",
                status=DeliveryAttemptStatus.FAILED,
                drain_id="drain-before-final",
            ),
        ),
    )

    assert reduction.selected_event_count == 3
    assert reduction.pending_event_count == 0
    assert reduction.final_drain_attempt_count == 3
    assert reduction.attempted_event_count == 2
    assert reduction.failed_event_count == 0
    assert reduction.drain_failure_percentage == 0.0
    assert reduction.oldest_pending_age_seconds is None
    assert reduction.attempted_event_ids == ("event-1", "event-2")
    assert reduction.failed_event_ids == ()


def test_empty_pending_has_no_age_or_failure_denominator() -> None:
    reduction = _reduce((_event("event-1", pending=False),), ())

    assert reduction.pending_event_count == 0
    assert reduction.oldest_pending_age_seconds is None
    assert reduction.drain_failure_percentage is None


def test_duplicate_or_conflicting_identities_fail_closed() -> None:
    event = _event("event-1", pending=True)
    attempt = _attempt("attempt-1", "event-1", status=DeliveryAttemptStatus.FAILED)

    with pytest.raises(PrototypeContractError, match="event identity"):
        _reduce((event, replace(event, destination="other")), ())
    with pytest.raises(PrototypeContractError, match="attempt identity"):
        _reduce(
            (event,),
            (
                attempt,
                replace(attempt, attempted_at=START + timedelta(minutes=6)),
            ),
        )


def test_attempts_must_be_within_source_window() -> None:
    with pytest.raises(PrototypeContractError, match="source_time > start"):
        _reduce(
            (_event("event-1", pending=True),),
            (
                _attempt(
                    "attempt-before-window",
                    "event-1",
                    status=DeliveryAttemptStatus.FAILED,
                    at=START,
                ),
            ),
        )


def test_failed_final_drain_event_must_remain_in_pending_snapshot() -> None:
    with pytest.raises(PrototypeContractError, match="final failed delivery population"):
        _reduce(
            (_event("event-1", pending=False),),
            (_attempt("attempt-1", "event-1", status=DeliveryAttemptStatus.FAILED),),
        )


def test_reduction_is_deterministic_regardless_of_input_order() -> None:
    events = (_event("event-b", pending=False), _event("event-a", pending=True))
    attempts = (
        _attempt(
            "attempt-b-retry",
            "event-b",
            status=DeliveryAttemptStatus.DELIVERED,
            at=START + timedelta(minutes=6),
        ),
        _attempt("attempt-b-initial", "event-b", status=DeliveryAttemptStatus.FAILED),
        _attempt("attempt-a", "event-a", status=DeliveryAttemptStatus.FAILED),
    )

    expected = _reduce(events, attempts)
    assert expected == _reduce(tuple(reversed(events)), tuple(reversed(attempts)))


def test_proof_blocks_missing_boundaries_and_nonfresh_evidence() -> None:
    blocked = build_outbox_proof(
        run_id="run-5",
        provenance=EvidenceProvenance.RETAINED,
        source_boundary="outbox rows > start and <= end",
        outbox_boundary=OutboxEvidenceBoundary(
            events=None,
            attempts=None,
            final_drain_id=None,
            source_start=None,
            source_end=None,
            scan_completed_at=None,
        ),
    )
    assert blocked.result is ExperimentResult.BLOCKED
    assert blocked.blocked_boundaries == (
        "events",
        "attempts",
        "final drain id",
        "source start",
        "source end",
        "scan completed at",
    )

    retained = build_outbox_proof(
        run_id="run-5",
        provenance=EvidenceProvenance.RETAINED,
        source_boundary="outbox rows > start and <= end",
        outbox_boundary=_boundary((_event("event-1", pending=True),), ()),
    )
    assert retained.result is ExperimentResult.BLOCKED
    assert retained.blocked_boundaries == ("fresh-real outbox evidence",)


def test_proof_fails_falsified_reconciliation_and_proves_fresh_real() -> None:
    failed = build_outbox_proof(
        run_id="run-5",
        provenance=EvidenceProvenance.FRESH_REAL,
        source_boundary="outbox rows > start and <= end",
        outbox_boundary=_boundary(
            (_event("event-1", pending=False),),
            (_attempt("attempt-1", "event-1", status=DeliveryAttemptStatus.FAILED),),
        ),
    )
    assert failed.result is ExperimentResult.FAILED

    proven = build_outbox_proof(
        run_id="run-5",
        provenance=EvidenceProvenance.FRESH_REAL,
        source_boundary="outbox rows > start and <= end",
        outbox_boundary=_boundary((_event("event-1", pending=True),), ()),
    )
    assert proven.result is ExperimentResult.PROVEN


def test_terminal_delivery_statuses_and_redacted_errors_conserve_population() -> None:
    reduction = _reduce(
        (
            _event("event-failed", pending=True),
            _event("event-delivered", pending=False),
        ),
        (
            _attempt(
                "attempt-failed",
                "event-failed",
                status=DeliveryAttemptStatus.FAILED,
            ),
            _attempt(
                "attempt-delivered",
                "event-delivered",
                status=DeliveryAttemptStatus.DELIVERED,
            ),
        ),
    )

    assert reduction.attempted_event_count == 2
    assert reduction.failed_event_count == 1
    assert reduction.failed_event_ids == ("event-failed",)
    with pytest.raises(PrototypeContractError, match="redacted classifier"):
        DeliveryAttempt(
            attempt_id="attempt-secret",
            event_id="event-failed",
            drain_id="drain-final",
            attempted_at=START + timedelta(minutes=5),
            status=DeliveryAttemptStatus.FAILED,
            error_class="timeout: customer@example.com",
        )


def test_e5_direct_sql_and_independent_oracle_reconcile_exact_typed_scalars() -> None:
    population = E5RemotePopulation(
        event_ids=("event-failed", "event-delivered"),
        final_drain_id="drain-final",
        source_start=START,
        source_end=END,
        scan_completed_at=COMPLETED,
    )
    oracle = e5_local_oracle(
        events=(
            _event("event-failed", pending=True),
            _event("event-delivered", pending=False),
        ),
        attempts=(
            _attempt("attempt-failed", "event-failed", status=DeliveryAttemptStatus.FAILED),
            _attempt(
                "attempt-delivered",
                "event-delivered",
                status=DeliveryAttemptStatus.DELIVERED,
            ),
        ),
        population=population,
    )
    assert oracle is not None
    assert E5_REMOTE_SQL.count("event_id IN exact_event_ids") == 2
    assert "drain_id = exact_final_drain_id" in E5_REMOTE_SQL
    remote = parse_e5_remote_result(
        (
            {
                "selected_event_count": 2,
                "pending_event_count": 1,
                "attempted_event_count": 2,
                "failed_event_count": 1,
                "final_drain_attempt_count": 2,
                "oldest_pending_age_seconds": 570.0,
                "drain_failure_percentage": 50.0,
            },
        )
    )
    assert remote.pending_event_count == oracle.pending_event_count
    assert remote.attempted_event_count == oracle.attempted_event_count
    assert remote.failed_event_count == oracle.failed_event_count
    assert remote.final_drain_attempt_count == oracle.final_drain_attempt_count
    assert remote.oldest_pending_age_seconds == oracle.oldest_pending_age_seconds
    assert remote.drain_failure_percentage == oracle.drain_failure_percentage


def test_e5_terminal_attempt_status_is_last_ordered_attempt_per_event() -> None:
    population = E5RemotePopulation(
        event_ids=("event-1",),
        final_drain_id="drain-final",
        source_start=START,
        source_end=END,
        scan_completed_at=COMPLETED,
    )
    reduction = e5_local_oracle(
        events=(_event("event-1", pending=False),),
        attempts=(
            _attempt(
                "attempt-failed",
                "event-1",
                status=DeliveryAttemptStatus.FAILED,
                at=START + timedelta(minutes=4),
            ),
            _attempt(
                "attempt-delivered",
                "event-1",
                status=DeliveryAttemptStatus.DELIVERED,
                at=START + timedelta(minutes=5),
            ),
        ),
        population=population,
    )
    assert reduction is not None
    assert reduction.attempted_event_count == 1
    assert reduction.failed_event_count == 0
    assert reduction.final_drain_attempt_count == 2
    assert "PARTITION BY event_id ORDER BY attempted_at DESC, attempt_id DESC" in E5_REMOTE_SQL


def test_e5_duplicate_immutable_event_is_idempotent_but_conflict_rejects() -> None:
    population = E5RemotePopulation(
        event_ids=("event-1",),
        final_drain_id="drain-final",
        source_start=START,
        source_end=END,
        scan_completed_at=COMPLETED,
    )
    event = _event("event-1", pending=True)
    reduction = e5_local_oracle(
        events=(event, event),
        attempts=(),
        population=population,
    )
    assert reduction is not None
    assert reduction.selected_event_count == 1

    with pytest.raises(PrototypeContractError, match="conflicting payloads"):
        e5_local_oracle(
            events=(event, _event("event-1", pending=False)),
            attempts=(),
            population=population,
        )


def test_e5_direct_authority_fails_closed_for_missing_bounds_and_invalid_remote_types() -> None:
    assert (
        e5_local_oracle(
            events=(_event("event-1", pending=True),),
            attempts=(),
            population=E5RemotePopulation(
                event_ids=("event-1",),
                final_drain_id=None,
                source_start=START,
                source_end=END,
                scan_completed_at=COMPLETED,
            ),
        )
        is None
    )
    with pytest.raises(PrototypeContractError, match="exact integer"):
        parse_e5_remote_result(
            (
                {
                    "selected_event_count": 1.0,
                    "pending_event_count": 1,
                    "attempted_event_count": 0,
                    "failed_event_count": 0,
                    "final_drain_attempt_count": 0,
                    "oldest_pending_age_seconds": 30.0,
                    "drain_failure_percentage": None,
                },
            )
        )

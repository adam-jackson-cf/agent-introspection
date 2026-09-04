"""Pure oracle for the E-Pipeline-5 immutable outbox experiment."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from experiments.dashboard_prototype.contracts import (
    EvidenceProvenance,
    ExperimentResult,
    PrototypeContractError,
)
from experiments.dashboard_prototype.pipeline_common import (
    PipelineExperimentId,
    PipelineExperimentProof,
)


class DeliveryAttemptStatus(StrEnum):
    """Redacted terminal delivery state recorded for one delivery attempt."""

    DELIVERED = "delivered"
    FAILED = "failed"


_REDACTED_ERROR_CLASS = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    """Immutable durable outbox event selected for the bounded snapshot."""

    event_id: str
    created_at: datetime
    destination: str
    event_type: str
    is_pending: bool

    def __post_init__(self) -> None:
        _require_identifier(self.event_id, "event identity")
        _require_time(self.created_at, "event creation time")
        _require_identifier(self.destination, "event destination")
        _require_identifier(self.event_type, "event type")
        if not isinstance(self.is_pending, bool):
            raise PrototypeContractError("event pending state must be boolean")


@dataclass(frozen=True, slots=True)
class DeliveryAttempt:
    """One final-drain delivery attempt for an immutable outbox event."""

    attempt_id: str
    event_id: str
    drain_id: str
    attempted_at: datetime
    status: DeliveryAttemptStatus
    error_class: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.attempt_id, "attempt identity")
        _require_identifier(self.event_id, "attempt event identity")
        _require_identifier(self.drain_id, "drain identity")
        _require_time(self.attempted_at, "attempt time")
        if not isinstance(self.status, DeliveryAttemptStatus):
            raise PrototypeContractError("attempt status must be a delivery attempt status")
        if self.status is DeliveryAttemptStatus.FAILED:
            if not isinstance(self.error_class, str) or not _REDACTED_ERROR_CLASS.fullmatch(
                self.error_class
            ):
                raise PrototypeContractError(
                    "failed attempt error class must be a redacted classifier"
                )
        elif self.error_class is not None:
            raise PrototypeContractError("delivered attempt cannot retain an error class")


@dataclass(frozen=True, slots=True)
class OutboxEvidenceBoundary:
    """Bounded outbox rows and final-drain identity for one proof."""

    events: Iterable[OutboxEvent] | None
    attempts: Iterable[DeliveryAttempt] | None
    final_drain_id: str | None
    source_start: datetime | None
    source_end: datetime | None
    scan_completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class OutboxReduction:
    """Population-conserving, deterministic final-drain accounting."""

    selected_event_count: int
    pending_event_count: int
    attempted_event_count: int
    failed_event_count: int
    final_drain_attempt_count: int
    oldest_pending_age_seconds: float | None
    drain_failure_percentage: float | None
    selected_event_ids: tuple[str, ...]
    attempted_event_ids: tuple[str, ...]
    final_drain_attempt_ids: tuple[str, ...]
    failed_event_ids: tuple[str, ...]


def reduce_outbox(*, boundary: OutboxEvidenceBoundary) -> OutboxReduction:
    """Reconcile selected events and one exact final-drain attempt population."""
    events, attempts, final_drain_id, source_start, source_end, scan_completed_at = (
        _reduction_values(boundary)
    )
    _validate_reduction_window(
        final_drain_id=final_drain_id,
        source_start=source_start,
        source_end=source_end,
        scan_completed_at=scan_completed_at,
    )
    event_by_id = _index_events(events, source_start, source_end)
    final_attempts = _select_final_attempts(
        attempts=attempts,
        event_by_id=event_by_id,
        final_drain_id=final_drain_id,
        source_start=source_start,
        source_end=source_end,
    )
    return _reduce_final_attempts(event_by_id, final_attempts, scan_completed_at)


def build_outbox_proof(
    *,
    run_id: str,
    provenance: EvidenceProvenance,
    source_boundary: str,
    outbox_boundary: OutboxEvidenceBoundary,
) -> PipelineExperimentProof:
    """Build a fail-closed proof from already-bounded durable outbox rows."""
    missing_boundaries = _missing_outbox_boundaries(outbox_boundary)
    if missing_boundaries:
        return PipelineExperimentProof(
            experiment_id=PipelineExperimentId.OUTBOX,
            run_id=run_id,
            result=ExperimentResult.BLOCKED,
            provenance=provenance,
            source_boundary=source_boundary,
            metrics={},
            assertions={"required_boundaries_present": False},
            evidence_ids=(),
            blocked_boundaries=missing_boundaries,
            proposal="Await the exact bounded outbox and final-drain evidence.",
        )

    try:
        reduction = reduce_outbox(boundary=outbox_boundary)
    except PrototypeContractError:
        return PipelineExperimentProof(
            experiment_id=PipelineExperimentId.OUTBOX,
            run_id=run_id,
            result=ExperimentResult.FAILED,
            provenance=provenance,
            source_boundary=source_boundary,
            metrics={},
            assertions={"outbox_reconciliation": False},
            evidence_ids=(),
            blocked_boundaries=(),
            proposal="Reject the bounded outbox evidence because reconciliation was falsified.",
        )

    return _proof_from_reduction(
        run_id=run_id,
        provenance=provenance,
        source_boundary=source_boundary,
        reduction=reduction,
    )


def _reduction_values(
    boundary: OutboxEvidenceBoundary,
) -> tuple[
    Iterable[OutboxEvent],
    Iterable[DeliveryAttempt],
    str,
    datetime,
    datetime,
    datetime,
]:
    if _missing_outbox_boundaries(boundary):
        raise PrototypeContractError("outbox reduction requires complete boundaries")
    if (
        boundary.events is None
        or boundary.attempts is None
        or boundary.final_drain_id is None
        or boundary.source_start is None
        or boundary.source_end is None
        or boundary.scan_completed_at is None
    ):
        raise AssertionError("complete boundaries must include all reduction values")
    return (
        boundary.events,
        boundary.attempts,
        boundary.final_drain_id,
        boundary.source_start,
        boundary.source_end,
        boundary.scan_completed_at,
    )


def _validate_reduction_window(
    *,
    final_drain_id: str,
    source_start: datetime,
    source_end: datetime,
    scan_completed_at: datetime,
) -> None:
    _require_identifier(final_drain_id, "final drain identity")
    _require_time(source_start, "source start")
    _require_time(source_end, "source end")
    _require_time(scan_completed_at, "scan completion time")
    if source_start >= source_end:
        raise PrototypeContractError("source boundary must have a positive duration")
    if scan_completed_at < source_end:
        raise PrototypeContractError("scan completion cannot precede source end")


def _index_events(
    events: Iterable[OutboxEvent], source_start: datetime, source_end: datetime
) -> dict[str, OutboxEvent]:
    event_by_id: dict[str, OutboxEvent] = {}
    for event in events:
        _require_membership(event.created_at, source_start, source_end, "event creation time")
        if event.event_id in event_by_id:
            raise PrototypeContractError("outbox event identity must be unique and immutable")
        event_by_id[event.event_id] = event
    return event_by_id


def _select_final_attempts(
    *,
    attempts: Iterable[DeliveryAttempt],
    event_by_id: dict[str, OutboxEvent],
    final_drain_id: str,
    source_start: datetime,
    source_end: datetime,
) -> list[DeliveryAttempt]:
    attempt_by_id: dict[str, DeliveryAttempt] = {}
    final_attempts: list[DeliveryAttempt] = []
    for attempt in sorted(
        attempts,
        key=lambda attempt: (
            attempt.event_id,
            attempt.attempted_at,
            attempt.attempt_id,
        ),
    ):
        _require_membership(attempt.attempted_at, source_start, source_end, "attempt time")
        if attempt.attempt_id in attempt_by_id:
            raise PrototypeContractError("delivery attempt identity must be unique")
        attempt_by_id[attempt.attempt_id] = attempt
        if attempt.drain_id == final_drain_id:
            if attempt.event_id not in event_by_id:
                raise PrototypeContractError("final drain attempt references an unselected event")
            final_attempts.append(attempt)
    _require_attempt_ordering(final_attempts)
    return final_attempts


def _reduce_final_attempts(
    event_by_id: dict[str, OutboxEvent],
    final_attempts: list[DeliveryAttempt],
    scan_completed_at: datetime,
) -> OutboxReduction:
    pending_ids = {event.event_id for event in event_by_id.values() if event.is_pending}
    final_attempt_by_event = {attempt.event_id: attempt for attempt in final_attempts}
    attempted_ids = set(final_attempt_by_event)
    failed_ids = {
        event_id
        for event_id, attempt in final_attempt_by_event.items()
        if attempt.status is DeliveryAttemptStatus.FAILED
    }
    delivered_ids = attempted_ids - failed_ids
    if failed_ids != pending_ids & attempted_ids:
        raise PrototypeContractError(
            "final failed delivery population must match pending attempted events"
        )
    if delivered_ids & pending_ids:
        raise PrototypeContractError("final delivered population cannot remain pending")

    pending_created_at = [event.created_at for event in event_by_id.values() if event.is_pending]
    oldest_pending_age_seconds = _oldest_pending_age(pending_created_at, scan_completed_at)
    return OutboxReduction(
        selected_event_count=len(event_by_id),
        pending_event_count=len(pending_ids),
        attempted_event_count=len(attempted_ids),
        failed_event_count=len(failed_ids),
        final_drain_attempt_count=len(final_attempts),
        oldest_pending_age_seconds=oldest_pending_age_seconds,
        drain_failure_percentage=(
            100.0 * len(failed_ids) / len(attempted_ids) if attempted_ids else None
        ),
        selected_event_ids=tuple(sorted(event_by_id)),
        attempted_event_ids=tuple(sorted(attempted_ids)),
        failed_event_ids=tuple(sorted(failed_ids)),
        final_drain_attempt_ids=tuple(sorted(attempt.attempt_id for attempt in final_attempts)),
    )


def _oldest_pending_age(
    pending_created_at: list[datetime], scan_completed_at: datetime
) -> float | None:
    if not pending_created_at:
        return None
    oldest_pending_age_seconds = (scan_completed_at - min(pending_created_at)).total_seconds()
    if oldest_pending_age_seconds < 0:
        raise PrototypeContractError("pending age cannot be negative")
    return oldest_pending_age_seconds


def _proof_from_reduction(
    *,
    run_id: str,
    provenance: EvidenceProvenance,
    source_boundary: str,
    reduction: OutboxReduction,
) -> PipelineExperimentProof:
    result = (
        ExperimentResult.PROVEN
        if provenance is EvidenceProvenance.FRESH_REAL
        else ExperimentResult.BLOCKED
    )
    return PipelineExperimentProof(
        experiment_id=PipelineExperimentId.OUTBOX,
        run_id=run_id,
        result=result,
        provenance=provenance,
        source_boundary=source_boundary,
        metrics={
            "selected_event_count": reduction.selected_event_count,
            "pending_event_count": reduction.pending_event_count,
            "attempted_event_count": reduction.attempted_event_count,
            "failed_event_count": reduction.failed_event_count,
            "final_drain_attempt_count": reduction.final_drain_attempt_count,
            "oldest_pending_age_seconds": reduction.oldest_pending_age_seconds,
            "drain_failure_percentage": reduction.drain_failure_percentage,
        },
        assertions={
            "event_identities_are_immutable": True,
            "final_drain_is_exactly_isolated": True,
            "terminal_delivery_statuses_reconcile": True,
            "failed_events_remain_pending": True,
            "attempt_population_reconciles": True,
            "errors_are_redacted_classifiers": True,
        },
        evidence_ids=(
            *reduction.selected_event_ids,
            *reduction.final_drain_attempt_ids,
        ),
        blocked_boundaries=(
            () if result is ExperimentResult.PROVEN else ("fresh-real outbox evidence",)
        ),
        proposal="Project immutable outbox events and exact final-drain reconciliation.",
    )


def _missing_outbox_boundaries(
    boundary: OutboxEvidenceBoundary,
) -> tuple[str, ...]:
    return _missing_boundaries(
        events=boundary.events,
        attempts=boundary.attempts,
        final_drain_id=boundary.final_drain_id,
        source_start=boundary.source_start,
        source_end=boundary.source_end,
        scan_completed_at=boundary.scan_completed_at,
    )


def _missing_boundaries(**boundaries: object) -> tuple[str, ...]:
    return tuple(
        name.replace("_", " ") for name, value in boundaries.items() if value is None or value == ""
    )


def _require_identifier(value: object, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise PrototypeContractError(f"{label} must be nonempty")


def _require_time(value: object, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PrototypeContractError(f"{label} must be timezone-aware")


def _require_membership(value: datetime, start: datetime, end: datetime, label: str) -> None:
    if not start < value <= end:
        raise PrototypeContractError(
            f"{label} must satisfy source_time > start AND source_time <= end"
        )


def _require_attempt_ordering(attempts: list[DeliveryAttempt]) -> None:
    by_event: dict[str, datetime] = {}
    for attempt in attempts:
        previous = by_event.get(attempt.event_id)
        if previous is not None and attempt.attempted_at < previous:
            raise PrototypeContractError("attempts for each event must be time ordered")
        by_event[attempt.event_id] = attempt.attempted_at


E5_REMOTE_RESULT_FIELDS = (
    "selected_event_count",
    "pending_event_count",
    "attempted_event_count",
    "failed_event_count",
    "final_drain_attempt_count",
    "oldest_pending_age_seconds",
    "drain_failure_percentage",
)

E5_REMOTE_SQL = """
WITH
    {event_ids:Array(String)} AS exact_event_ids,
    {final_drain_id:String} AS exact_final_drain_id,
    {source_start:DateTime64(9, 'UTC')} AS source_start,
    {source_end:DateTime64(9, 'UTC')} AS source_end,
    {scan_completed_at:DateTime64(9, 'UTC')} AS scan_completed_at,
    selected_events AS
    (
        SELECT
            event_id,
            created_at,
            is_pending
        FROM dashboard_outbox_events
        WHERE event_id IN exact_event_ids
          AND created_at > source_start
          AND created_at <= source_end
    ),
    final_drain_attempts AS
    (
        SELECT attempt_id, event_id, attempted_at, status
        FROM dashboard_outbox_delivery_attempts
        WHERE event_id IN exact_event_ids
          AND drain_id = exact_final_drain_id
          AND attempted_at > source_start
          AND attempted_at <= source_end
    ),
    terminal_attempts AS
    (
        SELECT attempt_id, event_id, status
        FROM
        (
            SELECT
                attempt_id,
                event_id,
                status,
                row_number() OVER (
                    PARTITION BY event_id ORDER BY attempted_at DESC, attempt_id DESC
                ) AS terminal_order
            FROM final_drain_attempts
        )
        WHERE terminal_order = 1
    )
SELECT
    toUInt64(count()) AS selected_event_count,
    toUInt64(countIf(is_pending)) AS pending_event_count,
    toUInt64((SELECT count() FROM terminal_attempts)) AS attempted_event_count,
    toUInt64((SELECT countIf(status = 'failed') FROM terminal_attempts)) AS failed_event_count,
    toUInt64((SELECT count() FROM final_drain_attempts)) AS final_drain_attempt_count,
    if(
        countIf(is_pending) = 0,
        CAST(NULL, 'Nullable(Float64)'),
        toFloat64(dateDiff('microsecond', minIf(created_at, is_pending), scan_completed_at))
            / 1000000.0
    ) AS oldest_pending_age_seconds,
    if(
        (SELECT count() FROM terminal_attempts) = 0,
        CAST(NULL, 'Nullable(Float64)'),
        100.0 * toFloat64((SELECT countIf(status = 'failed') FROM terminal_attempts))
            / toFloat64((SELECT count() FROM terminal_attempts))
    ) AS drain_failure_percentage
FROM selected_events
""".strip()


@dataclass(frozen=True, slots=True)
class E5RemotePopulation:
    """Exact immutable IDs and bounds supplied to the E-Pipeline-5 SQL calculation."""

    event_ids: tuple[str, ...] | None
    final_drain_id: str | None
    source_start: datetime | None
    source_end: datetime | None
    scan_completed_at: datetime | None

    def sql_parameters(self) -> dict[str, object] | None:
        """Return typed SQL bindings, or fail closed when authority is incomplete."""
        if (
            self.event_ids is None
            or self.final_drain_id is None
            or self.source_start is None
            or self.source_end is None
            or self.scan_completed_at is None
        ):
            return None
        if len(set(self.event_ids)) != len(self.event_ids):
            raise PrototypeContractError("remote immutable event identities must be unique")
        for event_id in self.event_ids:
            _require_identifier(event_id, "remote immutable event identity")
        _validate_reduction_window(
            final_drain_id=self.final_drain_id,
            source_start=self.source_start,
            source_end=self.source_end,
            scan_completed_at=self.scan_completed_at,
        )
        return {
            "event_ids": self.event_ids,
            "final_drain_id": self.final_drain_id,
            "source_start": self.source_start,
            "source_end": self.source_end,
            "scan_completed_at": self.scan_completed_at,
        }


def parse_e5_remote_result(rows: Sequence[Mapping[str, object]]) -> OutboxReduction:
    """Parse the one aggregate E-Pipeline-5 result row with exact scalar types."""
    if len(rows) != 1:
        raise PrototypeContractError("E-Pipeline-5 remote calculation must return one row")
    row = rows[0]
    if set(row) != set(E5_REMOTE_RESULT_FIELDS):
        raise PrototypeContractError("E-Pipeline-5 remote result has an unexpected shape")
    counts = tuple(_require_exact_int(row[field], field) for field in E5_REMOTE_RESULT_FIELDS[:5])
    oldest_age = _require_optional_float(row["oldest_pending_age_seconds"], "oldest pending age")
    failure_percentage = _require_optional_float(
        row["drain_failure_percentage"], "drain failure percentage"
    )
    return OutboxReduction(
        selected_event_count=counts[0],
        pending_event_count=counts[1],
        attempted_event_count=counts[2],
        failed_event_count=counts[3],
        final_drain_attempt_count=counts[4],
        oldest_pending_age_seconds=oldest_age,
        drain_failure_percentage=failure_percentage,
        selected_event_ids=(),
        attempted_event_ids=(),
        final_drain_attempt_ids=(),
        failed_event_ids=(),
    )


def e5_local_oracle(
    *,
    events: Iterable[OutboxEvent] | None,
    attempts: Iterable[DeliveryAttempt] | None,
    population: E5RemotePopulation,
) -> OutboxReduction | None:
    """Independently calculate E-Pipeline-5's aggregate result from immutable rows."""
    parameters = population.sql_parameters()
    if parameters is None or events is None or attempts is None:
        return None
    event_ids = population.event_ids
    final_drain_id = population.final_drain_id
    source_start = population.source_start
    source_end = population.source_end
    scan_completed_at = population.scan_completed_at
    assert event_ids is not None
    assert final_drain_id is not None
    assert source_start is not None
    assert source_end is not None
    assert scan_completed_at is not None
    selected = _select_oracle_events(events, frozenset(event_ids), source_start, source_end)
    final_attempts: list[DeliveryAttempt] = []
    attempt_ids: set[str] = set()
    for attempt in attempts:
        if attempt.drain_id != final_drain_id:
            continue
        _require_membership(attempt.attempted_at, source_start, source_end, "attempt time")
        if attempt.attempt_id in attempt_ids:
            raise PrototypeContractError("oracle delivery attempt identity must be unique")
        attempt_ids.add(attempt.attempt_id)
        if attempt.event_id not in selected:
            raise PrototypeContractError(
                "oracle final drain references an unselected immutable event"
            )
        final_attempts.append(attempt)
    final_attempts.sort(
        key=lambda attempt: (attempt.event_id, attempt.attempted_at, attempt.attempt_id)
    )
    return _reduce_final_attempts(selected, final_attempts, scan_completed_at)


def _select_oracle_events(
    events: Iterable[OutboxEvent],
    selected_ids: frozenset[str],
    source_start: datetime,
    source_end: datetime,
) -> dict[str, OutboxEvent]:
    selected: dict[str, OutboxEvent] = {}
    for event in events:
        if event.event_id not in selected_ids:
            continue
        existing = selected.get(event.event_id)
        if existing is not None:
            if existing != event:
                raise PrototypeContractError(
                    "oracle immutable event identity has conflicting payloads"
                )
            continue
        _require_membership(event.created_at, source_start, source_end, "event creation time")
        selected[event.event_id] = event
    if set(selected) != selected_ids:
        raise PrototypeContractError("oracle lacks an exact immutable outbox event population")
    return selected


def _require_exact_int(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise PrototypeContractError(f"{label} must be a nonnegative exact integer")
    return value


def _require_optional_float(value: object, label: str) -> float | None:
    if value is None:
        return None
    if type(value) is not float:
        raise PrototypeContractError(f"{label} must be a float or null")
    return value

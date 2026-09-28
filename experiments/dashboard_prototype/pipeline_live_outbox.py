"""Bounded live SQLite extraction for the immutable outbox experiment."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum

from experiments.dashboard_prototype.contracts import (
    EvidenceProvenance,
    ExperimentResult,
    PrototypeContractError,
)
from experiments.dashboard_prototype.pipeline_common import PipelineExperimentId
from experiments.dashboard_prototype.pipeline_live_common import (
    LiveExperimentEvidence,
    LiveProofRequest,
)
from experiments.dashboard_prototype.pipeline_outbox import (
    DeliveryAttempt,
    DeliveryAttemptStatus,
    OutboxEvent,
    OutboxEvidenceBoundary,
    build_outbox_proof,
)

_EVENT_COLUMNS = (
    "event_id",
    "payload_json",
    "status",
    "attempt_count",
    "next_attempt_at",
    "created_at",
    "delivered_at",
    "destination",
    "event_type",
)
_ATTEMPT_TABLE = "otlp_outbox_delivery_attempts"
_DRAIN_TABLE = "otlp_outbox_drains"
_ATTEMPT_COLUMNS = (
    "attempt_id",
    "event_id",
    "drain_id",
    "attempted_at",
    "status",
    "error_class",
)
_DRAIN_COLUMNS = (
    "drain_id",
    "completed_at",
    "is_final",
    "failure_timing_authoritative",
)
_EVENT_STATUSES = frozenset({"pending", "delivered"})
_REMOTE_QUERY_ID = "pipeline-outbox-current-pending-v1"


class OutboxEventStatus(StrEnum):
    """Immutable terminal state from the selected durable outbox row."""

    PENDING = "pending"
    DELIVERED = "delivered"


@dataclass(frozen=True, slots=True)
class E5OutboxEventPrimitive:
    """One privacy-safe immutable event row in the exact E5 source population."""

    experiment_id: PipelineExperimentId
    event_id: str
    created_at: datetime
    destination: str
    event_type: str
    status: OutboxEventStatus


@dataclass(frozen=True, slots=True)
class E5DeliveryAttemptPrimitive:
    """One privacy-safe immutable delivery-attempt row from the exact final drain."""

    experiment_id: PipelineExperimentId
    attempt_id: str
    event_id: str
    drain_id: str
    attempted_at: datetime
    status: DeliveryAttemptStatus
    error_class: str | None


@dataclass(frozen=True, slots=True)
class E5FinalDrainPrimitive:
    """The immutable final-drain identity and bounded completion instant for E5."""

    experiment_id: PipelineExperimentId
    drain_id: str
    completed_at: datetime


E5OutboxAuthorityPrimitive = (
    E5OutboxEventPrimitive | E5DeliveryAttemptPrimitive | E5FinalDrainPrimitive
)


def extract(connection: sqlite3.Connection, request: LiveProofRequest) -> LiveExperimentEvidence:
    """Extract only the bounded, row-identifiable immutable outbox authority."""
    events, conflict = _events(connection, request)
    attempts, final_drain_id, scan_completed_at = _final_drain(connection, request)
    boundary = OutboxEvidenceBoundary(
        events=events,
        attempts=attempts,
        final_drain_id=final_drain_id,
        source_start=request.start,
        source_end=request.end,
        scan_completed_at=scan_completed_at,
    )
    proof = build_outbox_proof(
        run_id=request.run_id,
        provenance=EvidenceProvenance.FRESH_REAL,
        source_boundary=request.source_boundary,
        outbox_boundary=boundary,
    )
    if conflict is not None:
        proof = replace(
            proof,
            result=ExperimentResult.FAILED,
            assertions={"outbox_rows_consistent": False},
            blocked_boundaries=(),
            proposal="Reject the bounded outbox evidence because immutable event rows conflict.",
        )

    primitives = _authority_primitives(
        events=events,
        attempts=attempts,
        final_drain_id=final_drain_id,
        scan_completed_at=scan_completed_at,
    )
    return LiveExperimentEvidence(
        proof=proof,
        primitives=primitives,  # type: ignore[arg-type]
        remote_query_id=_REMOTE_QUERY_ID if proof.result is ExperimentResult.PROVEN else None,
    )


def _events(
    connection: sqlite3.Connection, request: LiveProofRequest
) -> tuple[tuple[OutboxEvent, ...] | None, str | None]:
    if not _has_exact_columns(connection, "otlp_outbox", _EVENT_COLUMNS):
        return None, None
    try:
        rows = connection.execute(
            "SELECT event_id, created_at, destination, event_type, status FROM otlp_outbox "
            "WHERE created_at > ? AND created_at <= ? ORDER BY created_at, event_id",
            (request.start.isoformat(), request.end.isoformat()),
        ).fetchall()
    except sqlite3.OperationalError:
        return None, None

    by_id: dict[str, tuple[datetime, str, str, str]] = {}
    for event_id, created_at, destination, event_type, status in rows:
        try:
            parsed_id, details = _event_details(
                (event_id, created_at, destination, event_type, status)
            )
        except ValueError as error:
            return (), str(error)
        if parsed_id in by_id:
            return (), "duplicate immutable event"
        by_id[parsed_id] = details

    return tuple(
        OutboxEvent(
            event_id=_hash_identifier(event_id),
            created_at=created_at,
            destination=destination,
            event_type=event_type,
            is_pending=status == "pending",
        )
        for event_id, (created_at, destination, event_type, status) in by_id.items()
    ), None


def _event_details(
    row: tuple[object, ...],
) -> tuple[str, tuple[datetime, str, str, str]]:
    event_id, created_at, destination, event_type, status = row
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("invalid event identity")
    if not isinstance(destination, str) or not destination:
        raise ValueError("invalid destination")
    if not isinstance(event_type, str) or not event_type:
        raise ValueError("invalid event type")
    try:
        timestamp = _parse_time(created_at)
    except ValueError as error:
        raise ValueError("invalid creation time") from error
    current_status = str(status)
    if current_status not in _EVENT_STATUSES:
        raise ValueError("invalid status")
    return event_id, (timestamp, destination, event_type, current_status)


def _final_drain(
    connection: sqlite3.Connection, request: LiveProofRequest
) -> tuple[tuple[DeliveryAttempt, ...] | None, str | None, datetime | None]:
    """Read one complete final drain and its attempts from fixed allowlisted schemas."""
    if not _has_exact_columns(
        connection, _ATTEMPT_TABLE, _ATTEMPT_COLUMNS
    ) or not _has_exact_columns(connection, _DRAIN_TABLE, _DRAIN_COLUMNS):
        return None, None, None
    try:
        drains = connection.execute(
            "SELECT drain_id, completed_at FROM otlp_outbox_drains "
            "WHERE is_final = 1 AND failure_timing_authoritative = 1 "
            "AND completed_at > ? AND completed_at <= ? "
            "ORDER BY completed_at, drain_id",
            (request.start.isoformat(), request.end.isoformat()),
        ).fetchall()
        if len(drains) != 1:
            return None, None, None
        drain_id, completed_at = drains[0]
        scan_completed_at = _parse_time(completed_at)
        if not isinstance(drain_id, str) or not drain_id:
            return None, None, None
        rows = connection.execute(
            "SELECT attempt_id, event_id, drain_id, attempted_at, status, error_class "
            "FROM otlp_outbox_delivery_attempts "
            "WHERE drain_id = ? AND attempted_at > ? AND attempted_at <= ? "
            "ORDER BY event_id, attempted_at, attempt_id",
            (drain_id, request.start.isoformat(), request.end.isoformat()),
        ).fetchall()
        attempts = tuple(_delivery_attempt(row) for row in rows)
    except (sqlite3.Error, ValueError, PrototypeContractError):
        return None, None, None
    return attempts, _hash_identifier(drain_id), scan_completed_at


def _has_exact_columns(
    connection: sqlite3.Connection, table: str, columns: tuple[str, ...]
) -> bool:
    try:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error:
        return False
    return tuple(str(row[1]) for row in rows) == columns


def _delivery_attempt(row: tuple[object, ...]) -> DeliveryAttempt:
    attempt_id, event_id, drain_id, attempted_at, status, error_class = row
    if not isinstance(attempt_id, str) or not attempt_id:
        raise ValueError("delivery attempt identity must be a nonempty string")
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("delivery event identity must be a nonempty string")
    if not isinstance(drain_id, str) or not drain_id:
        raise ValueError("delivery drain identity must be a nonempty string")
    if error_class is not None and not isinstance(error_class, str):
        raise ValueError("delivery error class must be a redacted classifier")
    return DeliveryAttempt(
        attempt_id=_hash_identifier(attempt_id),
        event_id=_hash_identifier(event_id),
        drain_id=_hash_identifier(drain_id),
        attempted_at=_parse_time(attempted_at),
        status=DeliveryAttemptStatus(str(status)),
        error_class=error_class,
    )


def _authority_primitives(
    *,
    events: tuple[OutboxEvent, ...] | None,
    attempts: tuple[DeliveryAttempt, ...] | None,
    final_drain_id: str | None,
    scan_completed_at: datetime | None,
) -> tuple[E5OutboxAuthorityPrimitive, ...]:
    event_primitives = (
        ()
        if events is None
        else tuple(
            E5OutboxEventPrimitive(
                experiment_id=PipelineExperimentId.OUTBOX,
                event_id=event.event_id,
                created_at=event.created_at,
                destination=event.destination,
                event_type=event.event_type,
                status=(
                    OutboxEventStatus.PENDING if event.is_pending else OutboxEventStatus.DELIVERED
                ),
            )
            for event in events
        )
    )
    attempt_primitives = (
        ()
        if attempts is None
        else tuple(
            E5DeliveryAttemptPrimitive(
                experiment_id=PipelineExperimentId.OUTBOX,
                attempt_id=attempt.attempt_id,
                event_id=attempt.event_id,
                drain_id=attempt.drain_id,
                attempted_at=attempt.attempted_at,
                status=attempt.status,
                error_class=attempt.error_class,
            )
            for attempt in attempts
        )
    )
    final_drain = (
        ()
        if final_drain_id is None or scan_completed_at is None
        else (
            E5FinalDrainPrimitive(
                experiment_id=PipelineExperimentId.OUTBOX,
                drain_id=final_drain_id,
                completed_at=scan_completed_at,
            ),
        )
    )
    return (*event_primitives, *attempt_primitives, *final_drain)


def _parse_time(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("outbox created_at must be timezone-aware")
    return parsed.astimezone(UTC)


def _hash_identifier(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

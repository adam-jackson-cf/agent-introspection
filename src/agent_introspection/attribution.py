"""Context-driven canonical activity attribution and reconciliation."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from agent_introspection.database import (
    CanonicalActivity,
    CanonicalActivityWrite,
    CanonicalAttribution,
    persist_canonical_activity,
)
from agent_introspection.project_schema import AGENT_PROJECT_SCHEMA
from agent_introspection.telemetry import (
    CanonicalActivityVersionEvent,
    enqueue_canonical_activity_version,
)

_MISSING_CORRELATION = "missing_correlation_id"
_NO_CONTEXT_INTERVAL = "missing_workspace"
_AMBIGUOUS_CONTEXT_INTERVAL = "conflicting_correlation_id"


@dataclass(frozen=True, slots=True)
class Attribution:
    """The immutable canonical attribution tuple selected from session context."""

    state: str
    project_id: str | None
    method: str
    evidence_id: str | None
    reason_code: str | None

    def canonical(self, *, created_at: str) -> CanonicalAttribution:
        """Build the durable tuple for a canonical activity version."""
        return CanonicalAttribution(
            state=self.state,
            project_identity_id=self.project_id,
            method=self.method,
            evidence_id=self.evidence_id,
            reason_code=self.reason_code,
            created_at=created_at,
        )


def resolve_attribution(
    connection: sqlite3.Connection,
    *,
    producer: str | None,
    correlation_id: str | None,
    source_at: datetime,
) -> Attribution:
    """Resolve exactly one half-open session-context interval or one fixed reason."""
    if source_at.tzinfo is None:
        raise ValueError("source time must be timezone-aware")
    if producer not in {"claude-code", "codex-cli", "codex-app-server", "omp"} or not (
        correlation_id
    ):
        return Attribution("unresolved", None, "session_context", None, _MISSING_CORRELATION)
    rows = connection.execute(
        """
        SELECT event_id, project_id
        FROM session_context_intervals
        WHERE producer = ? AND session_id = ? AND started_at <= ?
          AND (ended_at IS NULL OR ? < ended_at)
        """,
        (
            producer,
            correlation_id,
            source_at.astimezone(UTC).isoformat(),
            source_at.astimezone(UTC).isoformat(),
        ),
    ).fetchall()
    if not rows:
        return Attribution("unresolved", None, "session_context", None, _NO_CONTEXT_INTERVAL)
    if len(rows) != 1:
        return Attribution("unresolved", None, "session_context", None, _AMBIGUOUS_CONTEXT_INTERVAL)
    event_id, project_id = rows[0]
    return Attribution("resolved", str(project_id), "session_context_interval", str(event_id), None)


def canonical_activity_event_attributes(
    connection: sqlite3.Connection,
    activity: CanonicalActivity,
    attribution: CanonicalAttribution,
) -> dict[str, str | int]:
    """Build the complete identity and attribution projection for canonical OTLP."""
    attributes: dict[str, str | int] = {
        "activity.producer": activity.producer,
        "activity.producer_surface": activity.producer_surface,
        "activity.correlation_id": activity.correlation_id,
        "activity.detector.id": activity.detector_id,
        "activity.detector.version": activity.detector_version,
        "activity.normalization.version": activity.normalization_version,
        "activity.attribution.state": attribution.state,
        "activity.attribution.method": attribution.method,
    }
    project_keys = AGENT_PROJECT_SCHEMA.attribute_keys
    if attribution.project_identity_id is None:
        attributes[project_keys["id"]] = "unresolved"
        attributes[project_keys["name"]] = "unresolved"
    else:
        row = connection.execute(
            "SELECT canonical_name FROM project_identities WHERE id = ?",
            (attribution.project_identity_id,),
        ).fetchone()
        if row is None:
            raise ValueError("resolved canonical attribution lacks a project identity")
        attributes[project_keys["id"]] = attribution.project_identity_id
        attributes[project_keys["name"]] = str(row[0])
        attributes["activity.attribution.project_identity_id"] = attribution.project_identity_id
    if attribution.evidence_id is not None:
        attributes["activity.attribution.evidence_id"] = attribution.evidence_id
    if attribution.reason_code is not None:
        attributes["activity.attribution.reason_code"] = attribution.reason_code
    return attributes


def _timestamp_ns(value: datetime) -> int:
    value_utc = value.astimezone(UTC)
    return (
        (value_utc.toordinal() - datetime(1970, 1, 1, tzinfo=UTC).toordinal()) * 86_400_000_000_000
        + value_utc.hour * 3_600_000_000_000
        + value_utc.minute * 60_000_000_000
        + value_utc.second * 1_000_000_000
        + value_utc.microsecond * 1_000
    )


def _schedule_recomputation(
    connection: sqlite3.Connection, *, activity_id: str, activity_version: int
) -> None:
    """Schedule both canonical projections in the caller-owned transaction."""
    connection.executemany(
        """
        INSERT INTO canonical_recomputation_schedule (
            activity_id, activity_version, aggregate_kind, scheduled_at
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT(activity_id, activity_version, aggregate_kind) DO NOTHING
        """,
        [
            (activity_id, activity_version, "findings", datetime.now(UTC).isoformat()),
            (activity_id, activity_version, "trends", datetime.now(UTC).isoformat()),
        ],
    )


def reconcile_activity(
    connection: sqlite3.Connection,
    *,
    activity: CanonicalActivity,
    source_at: datetime,
) -> CanonicalActivityWrite:
    """Reconcile one activity's canonical attribution and dependent projections."""
    if connection.in_transaction:
        raise ValueError("activity reconciliation requires no active transaction")
    if source_at.tzinfo is None:
        raise ValueError("source time must be timezone-aware")
    with connection:
        attribution = resolve_attribution(
            connection,
            producer=activity.producer,
            correlation_id=activity.correlation_id,
            source_at=source_at,
        ).canonical(created_at=datetime.now(UTC).isoformat())
        write = persist_canonical_activity(connection, activity, attribution)
        if not write.version_inserted:
            return write
        attributes = canonical_activity_event_attributes(connection, activity, attribution)
        event = CanonicalActivityVersionEvent(
            activity_id=write.activity_id,
            version=write.version,
            timestamp_ns=_timestamp_ns(source_at),
            attributes=attributes,
        )
        enqueue_canonical_activity_version(connection, event)
        _schedule_recomputation(
            connection, activity_id=write.activity_id, activity_version=write.version
        )
        return write

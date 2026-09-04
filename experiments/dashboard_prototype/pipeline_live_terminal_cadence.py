"""Read-only SQLite extraction for the terminal-cadence proof."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from experiments.dashboard_prototype.contracts import EvidenceProvenance
from experiments.dashboard_prototype.pipeline_common import PipelineExperimentId
from experiments.dashboard_prototype.pipeline_live_common import (
    LiveExperimentEvidence,
    LiveProofRequest,
    RemoteCalculationPrimitive,
)
from experiments.dashboard_prototype.pipeline_terminal_cadence import (
    TerminalCadenceProofRequest,
    TerminalObservation,
    TerminalSchedulePolicy,
    build_terminal_cadence_proof,
)

_POLICY_COLUMNS = frozenset(
    {
        "scheduled_at",
        "schedule_policy_identity",
        "schedule_interval_seconds",
        "schedule_timezone",
        "schedule_anchor_at",
    }
)
_REQUIRED_COLUMNS = frozenset({"id", "status", "completed_at"})


def extract(connection: sqlite3.Connection, request: LiveProofRequest) -> LiveExperimentEvidence:
    """Build fresh-real terminal evidence from durably recorded schedule inputs only."""
    columns = _scan_run_columns(connection)
    if not (columns >= _REQUIRED_COLUMNS and columns >= _POLICY_COLUMNS):
        return _evidence(request, (), (), None)

    rows = connection.execute(
        """
        SELECT id, status, completed_at, scheduled_at, schedule_policy_identity,
               schedule_interval_seconds, schedule_timezone, schedule_anchor_at
        FROM scan_runs
        WHERE completed_at > ? AND completed_at <= ?
        ORDER BY completed_at, id
        """,
        (request.start.isoformat(), request.end.isoformat()),
    ).fetchall()
    records, policy, observations = _durable_inputs(rows)
    return _evidence(request, records, observations, policy)


def _scan_run_columns(connection: sqlite3.Connection) -> frozenset[str]:
    table = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'scan_runs'"
    ).fetchone()
    if table is None:
        return frozenset()
    return frozenset(str(row[1]) for row in connection.execute("PRAGMA table_info(scan_runs)"))


@dataclass(frozen=True, slots=True)
class _DurableTerminalRecord:
    """One typed, privacy-safe terminal authority record from the single extraction."""

    durable_id_hash: str
    terminal_state: str
    completed_at: datetime
    scheduled_at: datetime
    policy_identity: str
    interval_seconds: int
    timezone: str
    anchor_at: datetime


def _durable_inputs(
    rows: list[tuple[object, ...]],
) -> tuple[
    tuple[_DurableTerminalRecord, ...],
    TerminalSchedulePolicy | None,
    tuple[TerminalObservation, ...],
]:
    parsed: list[_DurableTerminalRecord] = []
    try:
        for raw in rows:
            identity, status, completed, slot, policy_id, interval, timezone, anchor = raw
            if not (
                isinstance(identity, str)
                and identity
                and isinstance(status, str)
                and status
                and isinstance(policy_id, str)
                and policy_id
                and isinstance(timezone, str)
                and timezone
            ):
                return (), None, ()
            interval_seconds = _integer_or_none(interval)
            if interval_seconds is None:
                return (), None, ()
            parsed.append(
                _DurableTerminalRecord(
                    durable_id_hash=_hash_identifier(identity),
                    terminal_state=status,
                    completed_at=_parse_time(completed),
                    scheduled_at=_parse_time(slot),
                    policy_identity=policy_id,
                    interval_seconds=interval_seconds,
                    timezone=timezone,
                    anchor_at=_parse_time(anchor),
                )
            )
    except (TypeError, ValueError):
        return (), None, ()
    records = tuple(parsed)
    observations = tuple(
        TerminalObservation(
            record.durable_id_hash,
            record.terminal_state,
            record.completed_at,
            record.scheduled_at,
        )
        for record in records
    )
    policies = {
        (
            record.policy_identity,
            record.interval_seconds,
            record.timezone,
            record.anchor_at,
        )
        for record in records
    }
    if len(policies) != 1:
        return records, None, observations
    policy_id, interval, timezone, anchor = policies.pop()
    if interval <= 0:
        return records, None, observations
    return (
        records,
        TerminalSchedulePolicy(policy_id, timedelta(seconds=interval), timezone, anchor),
        observations,
    )


def _evidence(
    request: LiveProofRequest,
    records: tuple[_DurableTerminalRecord, ...],
    observations: tuple[TerminalObservation, ...],
    policy: TerminalSchedulePolicy | None,
) -> LiveExperimentEvidence:
    schedule = policy or TerminalSchedulePolicy("", timedelta(seconds=1), "UTC", request.start)
    proof = build_terminal_cadence_proof(
        TerminalCadenceProofRequest(
            request.run_id,
            EvidenceProvenance.FRESH_REAL,
            request.source_boundary,
            observations,
            schedule,
            request.start,
            request.end,
            request.end,
        )
    )
    primitives = tuple(
        _primitive(request, proof.experiment_id, ordinal, record)
        for ordinal, record in enumerate(records)
    )
    if policy is None:
        return LiveExperimentEvidence(proof, primitives, None)
    return LiveExperimentEvidence(
        proof,
        primitives,
        f"terminal-cadence-{_hash_identifier(proof.content_hash())}",
    )


def _primitive(
    request: LiveProofRequest,
    experiment_id: PipelineExperimentId,
    ordinal: int,
    record: _DurableTerminalRecord,
) -> RemoteCalculationPrimitive:
    return RemoteCalculationPrimitive(
        experiment_id,
        record.completed_at,
        ordinal,
        {
            "durable_id_hash": record.durable_id_hash,
            "terminal_state": record.terminal_state,
            "schedule_policy_identity": record.policy_identity,
            "schedule_timezone": record.timezone,
            "schedule_anchor_at": _timestamp_token(record.anchor_at),
            "scheduled_at": _timestamp_token(record.scheduled_at),
            "window_start": _timestamp_token(request.start),
            "window_end": _timestamp_token(request.end),
        },
        {
            "schedule_interval_seconds": record.interval_seconds,
            "scheduled_count": 1,
        },
    )


def _integer_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _parse_time(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("durable schedule times must be timezone-aware")
    return parsed.astimezone(UTC)


def _timestamp_token(value: datetime) -> str:
    """Encode a UTC instant as an allowlisted RFC 3339 token."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _hash_identifier(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

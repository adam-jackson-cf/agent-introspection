"""Bounded SQLite maintenance-ledger extraction for E-Pipeline-6."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Final, cast

from experiments.dashboard_prototype.contracts import EvidenceProvenance, ExperimentResult
from experiments.dashboard_prototype.pipeline_common import PipelineExperimentId
from experiments.dashboard_prototype.pipeline_ledger import (
    LedgerCheckResult,
    LedgerCheckType,
    LedgerObservation,
    LedgerReductionRequest,
    MigrationState,
    build_ledger_proof,
    reduce_ledger_observations,
)
from experiments.dashboard_prototype.pipeline_live_common import (
    LiveExperimentEvidence,
    LiveProofRequest,
    RemoteCalculationPrimitive,
)

_TABLE: Final = "maintenance_observations"
_COLUMNS: Final = (
    "event_id",
    "database_identity",
    "check_type",
    "completed_at",
    "result",
    "backup_completed_at",
    "database_bytes",
    "wal_bytes",
    "freelist_count",
    "page_count",
    "migration_state",
    "runtime_host",
)
_SAFE_EVENT_ID: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z", re.ASCII)
_REMOTE_QUERY_ID: Final = "p12-ledger-v3"


@dataclass(frozen=True, slots=True)
class LedgerMaintenancePrimitive:
    """Complete immutable E6 maintenance authority for one source observation."""

    experiment_id: PipelineExperimentId
    event_id: str
    database_identity: str
    check_type: LedgerCheckType
    completed_at: datetime
    result: LedgerCheckResult
    backup_completed_at: datetime
    database_bytes: int
    wal_bytes: int
    freelist_count: int
    page_count: int
    migration_state: MigrationState
    runtime_host: str


@dataclass(frozen=True, slots=True)
class _LedgerSourceObservation:
    observation: LedgerObservation
    primitive: LedgerMaintenancePrimitive | None


def extract(connection: sqlite3.Connection, request: LiveProofRequest) -> LiveExperimentEvidence:
    """Emit every complete bounded maintenance observation for the direct E6 oracle."""
    source_observations = _extract_observations(connection, request.start, request.end)
    reduction = reduce_ledger_observations(
        LedgerReductionRequest(
            observations=tuple(row.observation for row in source_observations),
            start=request.start,
            end=request.end,
            evaluated_at=request.end,
            stale_check_after=timedelta(),
            stale_backup_after=timedelta(),
        )
    )
    proof = build_ledger_proof(
        reduction,
        run_id=request.run_id,
        provenance=EvidenceProvenance.FRESH_REAL,
        source_boundary="maintenance_observations.completed_at > start AND <= end",
    )
    primitives = tuple(row.primitive for row in source_observations if row.primitive is not None)
    event_ids = tuple(row.event_id for row in primitives)
    if proof.result is not ExperimentResult.BLOCKED and (
        len(primitives) != len(source_observations) or len(set(event_ids)) != len(event_ids)
    ):
        proof = replace(
            proof,
            result=ExperimentResult.BLOCKED,
            blocked_boundaries=("immutable_observation_id",),
            proposal=(
                "Collect distinct immutable event IDs for every bounded maintenance observation."
            ),
        )
        primitives = ()
    return LiveExperimentEvidence(
        proof=proof,
        primitives=cast(tuple[RemoteCalculationPrimitive, ...], primitives),
        remote_query_id=_REMOTE_QUERY_ID if primitives else None,
    )


def _extract_observations(
    connection: sqlite3.Connection, start: datetime, end: datetime
) -> tuple[_LedgerSourceObservation, ...]:
    if not _has_complete_maintenance_table(connection):
        return ()
    rows = connection.execute(
        "SELECT event_id, database_identity, check_type, completed_at, result, "
        "backup_completed_at, database_bytes, wal_bytes, freelist_count, "
        "page_count, migration_state, runtime_host "
        "FROM maintenance_observations "
        "WHERE completed_at > ? AND completed_at <= ? "
        "ORDER BY completed_at, event_id",
        (start.isoformat(), end.isoformat()),
    )
    return tuple(_source_observation(row) for row in rows)


def _has_complete_maintenance_table(connection: sqlite3.Connection) -> bool:
    """Require the immutable ledger to expose exactly the public allowlist."""
    table = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (_TABLE,)
    ).fetchone()
    if table is None:
        return False
    columns = tuple(
        str(row[1]) for row in connection.execute("PRAGMA table_info(maintenance_observations)")
    )
    return columns == _COLUMNS


def _source_observation(row: tuple[object, ...]) -> _LedgerSourceObservation:
    (
        event_id,
        database_identity,
        check_type,
        completed_at,
        result,
        backup_completed_at,
        database_bytes,
        wal_bytes,
        freelist_count,
        page_count,
        migration_state,
        runtime_host,
    ) = row
    observation = LedgerObservation(
        database_identity=_hashed_identifier(database_identity),
        check_type=_check_type(check_type),
        completed_at=_timestamp(completed_at),
        result=_check_result(result),
        backup_completed_at=_timestamp(backup_completed_at),
        database_bytes=_integer(database_bytes),
        wal_bytes=_integer(wal_bytes),
        freelist_count=_integer(freelist_count),
        page_count=_integer(page_count),
        migration_state=_migration_state(migration_state),
        runtime_host=_hashed_identifier(runtime_host),
    )
    if (
        not isinstance(event_id, str)
        or not _SAFE_EVENT_ID.fullmatch(event_id)
        or observation.database_identity is None
        or observation.check_type is None
        or observation.completed_at is None
        or observation.result is None
        or observation.backup_completed_at is None
        or observation.database_bytes is None
        or observation.wal_bytes is None
        or observation.freelist_count is None
        or observation.page_count is None
        or observation.migration_state is None
        or observation.runtime_host is None
    ):
        return _LedgerSourceObservation(observation, None)
    return _LedgerSourceObservation(
        observation,
        LedgerMaintenancePrimitive(
            experiment_id=PipelineExperimentId.LEDGER,
            event_id=event_id,
            database_identity=observation.database_identity,
            check_type=observation.check_type,
            completed_at=observation.completed_at,
            result=observation.result,
            backup_completed_at=observation.backup_completed_at,
            database_bytes=observation.database_bytes,
            wal_bytes=observation.wal_bytes,
            freelist_count=observation.freelist_count,
            page_count=observation.page_count,
            migration_state=observation.migration_state,
            runtime_host=observation.runtime_host,
        ),
    )


def _hashed_identifier(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _check_type(value: object) -> LedgerCheckType | None:
    if not isinstance(value, str):
        return None
    try:
        return LedgerCheckType(value)
    except ValueError:
        return None


def _check_result(value: object) -> LedgerCheckResult | None:
    if not isinstance(value, str):
        return None
    try:
        return LedgerCheckResult(value)
    except ValueError:
        return None


def _migration_state(value: object) -> MigrationState | None:
    if not isinstance(value, str):
        return None
    try:
        return MigrationState(value)
    except ValueError:
        return None


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _integer(value: object) -> int | None:
    return value if type(value) is int else None

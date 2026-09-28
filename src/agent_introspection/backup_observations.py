"""Durable, immutable observations produced by database backup ownership."""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from agent_introspection.ledger_identity import database_identity
from agent_introspection.telemetry import DerivedEvent, enqueue_event

_BACKUP_EVENT_NAME = "introspection.database.backup.observed"


@dataclass(frozen=True, slots=True)
class BackupObservation:
    """The factual outcome captured at a backup ownership boundary."""

    backup_id: str
    database_id: str
    operation: str
    state: str
    completed_at_ns: int
    size_bytes: int | None
    verification: str
    failure_class: str | None


def _validate_observation(observation: BackupObservation) -> None:
    if str(uuid.UUID(observation.backup_id)) != observation.backup_id:
        raise ValueError("backup identity must be a canonical UUID")
    if observation.state not in {"succeeded", "failed"}:
        raise ValueError("backup state must be succeeded or failed")
    if observation.verification not in {"ok", "failed", "not_performed"}:
        raise ValueError("backup verification has an unsupported value")
    if not observation.operation:
        raise ValueError("backup operation is required")
    if observation.completed_at_ns <= 0:
        raise ValueError("backup completion time must be positive")
    if observation.size_bytes is not None and observation.size_bytes < 0:
        raise ValueError("backup size cannot be negative")
    if observation.state == "succeeded" and (
        observation.verification != "ok" or observation.failure_class is not None
    ):
        raise ValueError("successful backups require verified success without a failure class")
    if observation.state == "failed" and observation.failure_class is None:
        raise ValueError("failed backups require a safe failure class")


def record_backup_observation(
    connection: sqlite3.Connection, observation: BackupObservation
) -> str:
    _validate_observation(observation)
    if observation.database_id != database_identity(connection):
        raise ValueError("backup observation database identity does not match connection")

    backup_id = observation.backup_id
    attributes: dict[str, str | int | float | bool] = {
        "backup.state": observation.state,
        "backup.completed_at_ns": str(observation.completed_at_ns),
        "backup.operation": observation.operation,
        "backup.verification": observation.verification,
    }
    if observation.size_bytes is not None:
        attributes["backup.bytes"] = observation.size_bytes
    if observation.failure_class is not None:
        attributes["backup.failure_class"] = observation.failure_class
    event = DerivedEvent(
        scope="database-backup",
        entity_id=backup_id,
        entity_version=1,
        event_sequence=1,
        event_name=_BACKUP_EVENT_NAME,
        attributes=attributes,
        timestamp_ns=observation.completed_at_ns,
    )

    def write() -> str:
        event_id = enqueue_event(connection, event)
        connection.execute(
            """
            INSERT INTO backup_observations (
                backup_id, event_id, database_identity, operation, state,
                completed_at_ns, bytes, verification, failure_class
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                backup_id,
                event_id,
                observation.database_id,
                observation.operation,
                observation.state,
                str(observation.completed_at_ns),
                observation.size_bytes,
                observation.verification,
                observation.failure_class,
            ),
        )
        return event_id

    connection.execute("SAVEPOINT backup_observation")
    try:
        event_id = write()
        connection.execute("RELEASE SAVEPOINT backup_observation")
        return event_id
    except BaseException as observation_error:
        try:
            connection.execute("ROLLBACK TO SAVEPOINT backup_observation")
            connection.execute("RELEASE SAVEPOINT backup_observation")
        except BaseException as rollback_error:
            raise BaseExceptionGroup(
                "Backup observation persistence and rollback failed",
                [observation_error, rollback_error],
            ) from None
        raise


def latest_backup_observation(
    connection: sqlite3.Connection, database_path: Path
) -> Mapping[str, str | int] | None:
    """Return the latest actual backup observation for one canonical database."""
    identity = database_identity(connection, database_path=database_path)
    row = connection.execute(
        """
        SELECT event_id, state, completed_at_ns, bytes, verification
        FROM backup_observations
        WHERE database_identity = ?
        ORDER BY CAST(completed_at_ns AS INTEGER) DESC, backup_id DESC
        LIMIT 1
        """,
        (identity,),
    ).fetchone()
    if row is None:
        return None
    observation: dict[str, str | int] = {
        "backup.state": str(row[1]),
        "backup.completed_at_ns": str(row[2]),
        "backup.verification": str(row[4]),
        "backup.event_id": str(row[0]),
    }
    if row[3] is not None:
        observation["backup.bytes"] = int(row[3])
    return observation

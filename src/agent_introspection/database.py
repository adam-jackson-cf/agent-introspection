"""SQLite connection, integrity, backup, restore, and atomic persistence operations."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_introspection.migrations import apply_migrations


class DatabaseError(RuntimeError):
    """A database operation could not satisfy its safety contract."""


class DatabaseIntegrityError(DatabaseError):
    """SQLite reported corruption or an invalid persisted relationship."""


@dataclass(frozen=True, slots=True)
class ObservationRecord:
    """A fully normalized observation ready for durable persistence."""

    id: str
    scan_run_id: str
    detector_id: str
    detector_version: int
    category: str
    project_identity_id: str | None
    task_identity: str | None
    turn_identity: str | None
    occurred_at_ns: int
    fingerprint: str
    operation_kind: str
    target_kind: str
    normalized_target: str
    normalized_failure_class: str
    normalization_version: int
    membership_explanation: str
    attributes: Mapping[str, Any]
    created_at: str

    def values(self) -> tuple[object, ...]:
        return (
            self.id,
            self.scan_run_id,
            self.detector_id,
            self.detector_version,
            self.category,
            self.project_identity_id,
            self.task_identity,
            self.turn_identity,
            self.occurred_at_ns,
            self.fingerprint,
            self.operation_kind,
            self.target_kind,
            self.normalized_target,
            self.normalized_failure_class,
            self.normalization_version,
            self.membership_explanation,
            json.dumps(
                self.attributes,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ),
            self.created_at,
        )


@dataclass(frozen=True, slots=True)
class SourceWatermark:
    """The last source row durably included in an extraction transaction."""

    source: str
    timestamp_ns: int
    row_id: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class RestoreResult:
    """Paths proving a completed restore and its pre-restore safety backup."""

    database_path: Path
    safety_backup_path: Path | None


@dataclass(frozen=True, slots=True)
class MaintenanceResult:
    """Evidence from weekly integrity, analysis, and backup maintenance."""

    integrity_result: tuple[str, ...]
    backup_path: Path


@dataclass(frozen=True, slots=True)
class VacuumResult:
    """The eligibility and outcome of a manual compaction request."""

    free_page_ratio: float
    vacuumed: bool
    backup_path: Path | None


_OBSERVATION_COLUMNS = (
    "id, scan_run_id, detector_id, detector_version, category, project_identity_id, "
    "task_identity, turn_identity, occurred_at_ns, fingerprint, operation_kind, "
    "target_kind, normalized_target, normalized_failure_class, normalization_version, "
    "membership_explanation, attributes_json, created_at"
)


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def _require_idle(connection: sqlite3.Connection, operation: str) -> None:
    if connection.in_transaction:
        raise DatabaseError(f"{operation} requires a connection with no active transaction")


def connect_database(
    path: Path,
    *,
    busy_timeout_ms: int = 5_000,
) -> sqlite3.Connection:
    """Open the canonical on-disk database with all required SQLite protections."""

    if isinstance(busy_timeout_ms, bool) or busy_timeout_ms <= 0:
        raise ValueError("busy_timeout_ms must be a positive integer")
    database_path = path.expanduser().resolve(strict=False)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        database_path,
        timeout=busy_timeout_ms / 1_000,
        isolation_level="DEFERRED",
    )
    try:
        connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys = ON")
        foreign_keys = int(connection.execute("PRAGMA foreign_keys").fetchone()[0])
        if foreign_keys != 1:
            raise DatabaseError("SQLite foreign-key enforcement could not be enabled")
        journal_mode = str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0])
        if journal_mode.lower() != "wal":
            raise DatabaseError(f"SQLite WAL mode could not be enabled: {journal_mode}")
        apply_migrations(connection, database_path)
        return connection
    except BaseException:
        connection.close()
        raise


def _pragma_check(connection: sqlite3.Connection, pragma: str) -> tuple[str, ...]:
    _require_idle(connection, pragma)
    try:
        rows = tuple(str(row[0]) for row in connection.execute(f"PRAGMA {pragma}"))
    except sqlite3.Error as exc:
        raise DatabaseIntegrityError(f"SQLite {pragma} could not be completed") from exc
    if rows != ("ok",):
        raise DatabaseIntegrityError(f"SQLite {pragma} failed: {rows!r}")
    return rows


def quick_check(connection: sqlite3.Connection) -> tuple[str, ...]:
    """Run the mandatory pre-scan structural check and fail on any diagnostic."""

    return _pragma_check(connection, "quick_check")


def integrity_check(connection: sqlite3.Connection) -> tuple[str, ...]:
    """Run SQLite's complete integrity check and fail on any diagnostic."""

    return _pragma_check(connection, "integrity_check")


def _read_only_connection(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise DatabaseError(f"database file does not exist: {path}")
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def verify_database_file(path: Path) -> tuple[str, ...]:
    """Verify a closed database file without allowing SQLite to create it."""

    connection = _read_only_connection(path)
    try:
        result = integrity_check(connection)
        foreign_key_violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_violations:
            raise DatabaseIntegrityError(
                f"SQLite foreign_key_check failed: {foreign_key_violations!r}"
            )
        return result
    finally:
        connection.close()


def backup_database(connection: sqlite3.Connection, destination: Path) -> Path:
    """Create and verify one SQLite online backup without overwriting evidence."""

    _require_idle(connection, "online backup")
    integrity_check(connection)
    backup_path = destination.expanduser().resolve(strict=False)
    if backup_path.exists():
        raise DatabaseError(f"backup destination already exists: {backup_path}")
    source_row = connection.execute("PRAGMA database_list").fetchone()
    if source_row is not None and source_row[2]:
        source_path = Path(str(source_row[2])).resolve(strict=False)
        if source_path == backup_path:
            raise DatabaseError("backup destination must differ from the source database")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    destination_connection = sqlite3.connect(backup_path)
    try:
        connection.backup(destination_connection)
    except sqlite3.Error as exc:
        destination_connection.close()
        backup_path.unlink(missing_ok=True)
        raise DatabaseError(f"online backup failed: {backup_path}") from exc
    else:
        destination_connection.close()
    try:
        verify_database_file(backup_path)
    except BaseException:
        backup_path.unlink(missing_ok=True)
        raise
    return backup_path


def restore_database(
    database_path: Path,
    backup_path: Path,
    *,
    busy_timeout_ms: int = 5_000,
) -> RestoreResult:
    """Restore a verified backup atomically after preserving the current database."""

    if isinstance(busy_timeout_ms, bool) or busy_timeout_ms <= 0:
        raise ValueError("busy_timeout_ms must be a positive integer")
    target = database_path.expanduser().resolve(strict=False)
    source = backup_path.expanduser().resolve(strict=False)
    if target == source:
        raise DatabaseError("restore source and destination must differ")
    verify_database_file(source)
    target.parent.mkdir(parents=True, exist_ok=True)

    safety_backup: Path | None = None
    if target.exists():
        current = sqlite3.connect(target, timeout=busy_timeout_ms / 1_000)
        try:
            current.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
            current.execute("BEGIN EXCLUSIVE")
            current.rollback()
            safety_backup = target.with_name(f"{target.name}.pre-restore-{_utc_stamp()}.bak")
            backup_database(current, safety_backup)
        finally:
            current.close()

    temporary = target.with_name(f".{target.name}.restore-{uuid.uuid4().hex}.tmp")
    try:
        source_connection = _read_only_connection(source)
        temporary_connection = sqlite3.connect(temporary)
        try:
            source_connection.backup(temporary_connection)
        except sqlite3.Error as exc:
            raise DatabaseError(f"restore copy failed: {source}") from exc
        finally:
            temporary_connection.close()
            source_connection.close()
        verify_database_file(temporary)
        for suffix in ("-wal", "-shm"):
            Path(f"{target}{suffix}").unlink(missing_ok=True)
        os.replace(temporary, target)
        verify_database_file(target)
    finally:
        temporary.unlink(missing_ok=True)
    return RestoreResult(database_path=target, safety_backup_path=safety_backup)


def weekly_maintenance(
    connection: sqlite3.Connection,
    database_path: Path,
    *,
    backup_directory: Path | None = None,
) -> MaintenanceResult:
    """Run the weekly integrity check, ANALYZE, and verified online backup."""

    result = integrity_check(connection)
    with connection:
        connection.execute("ANALYZE")
    directory = (
        backup_directory.expanduser().resolve(strict=False)
        if backup_directory is not None
        else database_path.expanduser().resolve(strict=False).parent / "backups"
    )
    backup_path = directory / f"introspection-{_utc_stamp()}.sqlite3"
    return MaintenanceResult(result, backup_database(connection, backup_path))


def manual_vacuum(
    connection: sqlite3.Connection,
    database_path: Path,
    *,
    backup_directory: Path | None = None,
) -> VacuumResult:
    """VACUUM only above 25 percent free pages and after a verified backup."""

    _require_idle(connection, "VACUUM")
    page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
    free_pages = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
    free_page_ratio = free_pages / page_count if page_count else 0.0
    if free_page_ratio <= 0.25:
        return VacuumResult(free_page_ratio, False, None)
    directory = (
        backup_directory.expanduser().resolve(strict=False)
        if backup_directory is not None
        else database_path.expanduser().resolve(strict=False).parent / "backups"
    )
    backup_path = backup_database(connection, directory / f"pre-vacuum-{_utc_stamp()}.sqlite3")
    connection.execute("VACUUM")
    quick_check(connection)
    return VacuumResult(free_page_ratio, True, backup_path)


def persist_observations_and_watermark(
    connection: sqlite3.Connection,
    observations: Sequence[ObservationRecord],
    watermark: SourceWatermark,
    *,
    manage_transaction: bool = True,
) -> None:
    """Persist observations and their source watermark in one atomic transaction."""

    if manage_transaction:
        _require_idle(connection, "observation persistence")
    elif not connection.in_transaction:
        raise DatabaseError("shared observation persistence requires an active transaction")
    if not watermark.source or not watermark.row_id or watermark.timestamp_ns < 0:
        raise ValueError("source watermark requires source, row_id, and non-negative timestamp")
    ids = [observation.id for observation in observations]
    if any(not observation_id for observation_id in ids) or len(ids) != len(set(ids)):
        raise ValueError("observation IDs must be non-empty and unique per transaction")
    values = [(observation, observation.values()) for observation in observations]
    placeholders = ", ".join("?" for _ in range(18))
    try:
        if manage_transaction:
            connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            "SELECT timestamp_ns, row_id FROM source_watermarks WHERE source = ?",
            (watermark.source,),
        ).fetchone()
        if current is not None and (watermark.timestamp_ns, watermark.row_id) < (
            int(current[0]),
            str(current[1]),
        ):
            raise DatabaseError("source watermark cannot move backwards")
        for observation, row_values in values:
            cursor = connection.execute(
                f"INSERT INTO observations ({_OBSERVATION_COLUMNS}) "
                f"VALUES ({placeholders}) ON CONFLICT(id) DO NOTHING",
                row_values,
            )
            if cursor.rowcount == 0:
                existing = connection.execute(
                    f"SELECT {_OBSERVATION_COLUMNS} FROM observations WHERE id = ?",
                    (observation.id,),
                ).fetchone()
                if existing is None or tuple(existing) != row_values:
                    raise DatabaseError(
                        f"observation ID {observation.id!r} conflicts with persisted content"
                    )
        connection.execute(
            """
            INSERT INTO source_watermarks (source, timestamp_ns, row_id, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
                timestamp_ns = excluded.timestamp_ns,
                row_id = excluded.row_id,
                updated_at = excluded.updated_at
            """,
            (
                watermark.source,
                watermark.timestamp_ns,
                watermark.row_id,
                watermark.updated_at,
            ),
        )
        if manage_transaction:
            connection.commit()
    except BaseException:
        if manage_transaction and connection.in_transaction:
            connection.rollback()
        raise

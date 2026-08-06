"""Copy-only fresh-start rehearsal for the canonical SQLite store."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_introspection.config import DEFAULT_DATABASE_PATH
from agent_introspection.migrations import MIGRATIONS, apply_migrations


class FreshStartError(RuntimeError):
    """A fresh-start manifest or copy rehearsal is unsafe."""


@dataclass(frozen=True, slots=True)
class FreshStartManifest:
    source_migration_evidence: tuple[tuple[int, str, str], ...]
    approved_source_snapshots: tuple[tuple[Any, ...], ...]
    canonical_schema_identity: tuple[tuple[str, str, str], ...]
    checksum: str


def _json_value(value: object) -> object:
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    raise TypeError(f"unsupported SQLite value {type(value).__name__}")


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=_json_value).encode()
    ).hexdigest()


def _manifest_body(manifest: FreshStartManifest) -> dict[str, object]:
    body = asdict(manifest)
    body.pop("checksum")
    return body


def _schema_identity(connection: sqlite3.Connection) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (str(row[0]), str(row[1]), str(row[2]))
        for row in connection.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE type IN ('table', 'index', 'trigger') AND name NOT LIKE 'sqlite_%' "
            "ORDER BY type, name"
        )
    )


def _canonical_schema_identity() -> tuple[tuple[str, str, str], ...]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        for statement in MIGRATIONS[0].statements:
            connection.execute(statement)
        return _schema_identity(connection)
    finally:
        connection.close()


def _approved_snapshots(connection: sqlite3.Connection) -> tuple[tuple[Any, ...], ...]:
    try:
        return tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT id, source, fingerprint, schema_json, captured_at, approved_at, "
                "approved_by FROM source_schema_snapshots "
                "WHERE approved_at IS NOT NULL AND approved_by IS NOT NULL ORDER BY id"
            )
        )
    except sqlite3.Error as exc:
        raise FreshStartError("source database cannot provide approved source snapshots") from exc


def _source_migration_evidence(connection: sqlite3.Connection) -> tuple[tuple[int, str, str], ...]:
    try:
        return tuple(
            (int(row[0]), str(row[1]), str(row[2]))
            for row in connection.execute(
                "SELECT version, name, checksum FROM migrations ORDER BY version"
            )
        )
    except sqlite3.Error as exc:
        raise FreshStartError(
            "source database cannot provide migration integrity evidence"
        ) from exc


def build_fresh_start_manifest(source_database_path: Path) -> FreshStartManifest:
    """Capture approved source snapshots and source migration integrity evidence."""
    source = source_database_path.expanduser().resolve(strict=False)
    if not source.is_file():
        raise FreshStartError(f"source database does not exist: {source}")
    connection = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)
    try:
        manifest = FreshStartManifest(
            _source_migration_evidence(connection),
            _approved_snapshots(connection),
            _canonical_schema_identity(),
            "",
        )
        return FreshStartManifest(
            manifest.source_migration_evidence,
            manifest.approved_source_snapshots,
            manifest.canonical_schema_identity,
            _digest(_manifest_body(manifest)),
        )
    finally:
        connection.close()


def _validate_manifest(manifest: FreshStartManifest) -> None:
    if not isinstance(manifest, FreshStartManifest) or manifest.checksum != _digest(
        _manifest_body(manifest)
    ):
        raise FreshStartError("fresh-start manifest checksum is invalid")
    if not manifest.source_migration_evidence or not manifest.canonical_schema_identity:
        raise FreshStartError("fresh-start manifest is incomplete")


def _validate_source(connection: sqlite3.Connection, manifest: FreshStartManifest) -> None:
    if (
        _source_migration_evidence(connection) != manifest.source_migration_evidence
        or _approved_snapshots(connection) != manifest.approved_source_snapshots
    ):
        raise FreshStartError("source database does not exactly match fresh-start manifest")


def _cutoff_ns(cutoff: str) -> int:
    if not isinstance(cutoff, str) or not cutoff or cutoff.strip() != cutoff:
        raise FreshStartError("cutoff must be an RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FreshStartError("cutoff must be an RFC3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FreshStartError("cutoff must include a UTC offset")
    delta = parsed.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    cutoff_ns = (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000
    if cutoff_ns < 0:
        raise FreshStartError("cutoff must not precede the Unix epoch")
    return cutoff_ns


def _runtime_tables(connection: sqlite3.Connection) -> tuple[str, ...]:
    return tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT IN ('migrations', 'source_schema_snapshots', 'source_watermarks') "
            "ORDER BY name"
        )
    )


def _validate_target(
    connection: sqlite3.Connection, manifest: FreshStartManifest, cutoff: str, cutoff_ns: int
) -> None:
    if _schema_identity(connection) != manifest.canonical_schema_identity:
        raise FreshStartError("fresh-start copy schema does not match the canonical migration")
    if any(
        connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone() != (0,)
        for table in _runtime_tables(connection)
    ):
        raise FreshStartError("fresh-start copy retains runtime rows")
    if _approved_snapshots(connection) != manifest.approved_source_snapshots:
        raise FreshStartError("fresh-start copy did not preserve approved source snapshots")
    watermark = connection.execute(
        "SELECT timestamp_ns, row_id, updated_at FROM source_watermarks "
        "WHERE source = 'signoz_logs'"
    ).fetchone()
    if watermark != (cutoff_ns, "", cutoff):
        raise FreshStartError("fresh-start copy watermark does not equal cutoff")
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise FreshStartError("fresh-start copy failed SQLite integrity verification")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise FreshStartError("fresh-start copy violates foreign keys")


def rehearse_fresh_start_copy(
    source: Path, target: Path, manifest: FreshStartManifest, cutoff: str, marker: str
) -> FreshStartManifest:
    """Create a marked canonical database from approved source evidence only."""
    _validate_manifest(manifest)
    cutoff_ns = _cutoff_ns(cutoff)
    source_path = source.expanduser().resolve(strict=False)
    target_path = target.expanduser().resolve(strict=False)
    live_path = DEFAULT_DATABASE_PATH.expanduser().resolve(strict=False)
    if (
        not isinstance(marker, str)
        or not marker
        or marker not in target_path.name
        or target_path in (source_path, live_path)
        or target_path.exists()
    ):
        raise FreshStartError(
            "fresh-start target requires an explicit marked non-live nonexistent path"
        )
    if not source_path.is_file():
        raise FreshStartError(f"source database does not exist: {source_path}")
    source_connection = sqlite3.connect(f"{source_path.as_uri()}?mode=ro", uri=True)
    try:
        _validate_source(source_connection, manifest)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(target_path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            apply_migrations(connection, target_path)
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany(
                "INSERT INTO source_schema_snapshots "
                "(id, source, fingerprint, schema_json, captured_at, approved_at, approved_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                manifest.approved_source_snapshots,
            )
            connection.execute(
                "INSERT INTO source_watermarks (source, timestamp_ns, row_id, updated_at) "
                "VALUES ('signoz_logs', ?, '', ?)",
                (cutoff_ns, cutoff),
            )
            connection.commit()
            _validate_source(source_connection, manifest)
            _validate_target(connection, manifest, cutoff, cutoff_ns)
            return manifest
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
    finally:
        source_connection.close()

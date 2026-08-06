"""Immutable, copy-only fresh-start rehearsal for the SQLite analytical store."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from agent_introspection.config import DEFAULT_DATABASE_PATH


class FreshStartError(RuntimeError):
    """A fresh-start manifest or copy rehearsal is unsafe."""


# This explicit inventory is the contract.  It intentionally does not inspect unknown tables.
_TABLES: Final[tuple[str, ...]] = (
    "migrations",
    "scan_runs",
    "source_schema_snapshots",
    "source_watermarks",
    "project_identities",
    "observations",
    "evidence",
    "findings",
    "finding_membership",
    "trend_evaluations",
    "review_sessions",
    "model_runs",
    "model_budget_ledger",
    "model_capability_proofs",
    "semantic_classifications",
    "proposal_drafts",
    "proposals",
    "proposal_events",
    "otlp_outbox",
    "scheduler_leases",
    "analysis_generations",
    "analysis_generation_event_links",
    "analysis_generation_activations",
    "analysis_generation_current",
    "attribution_reanalysis_fact_sets",
    "attribution_reanalysis_facts",
    "session_context_events",
    "session_context_intervals",
    "project_evidence_intervals",
    "canonical_activities",
    "canonical_activity_versions",
    "canonical_recomputation_schedule",
    "canonical_activity_outbox_evidence",
    "canonical_rejections",
    "observation_activity_migration_manifest",
    "canonical_finding_membership",
)
_PRESERVED: Final[tuple[str, ...]] = ("migrations",)
_CLEARED: Final[tuple[str, ...]] = tuple(table for table in _TABLES if table not in _PRESERVED)
_DELETE_ORDER: Final[tuple[str, ...]] = (
    "canonical_activity_outbox_evidence",
    "analysis_generation_event_links",
    "analysis_generation_current",
    "analysis_generation_activations",
    "canonical_recomputation_schedule",
    "canonical_finding_membership",
    "observation_activity_migration_manifest",
    "canonical_activity_versions",
    "canonical_activities",
    "project_evidence_intervals",
    "session_context_intervals",
    "session_context_events",
    "attribution_reanalysis_facts",
    "attribution_reanalysis_fact_sets",
    "semantic_classifications",
    "model_budget_ledger",
    "model_runs",
    "model_capability_proofs",
    "proposal_events",
    "proposal_drafts",
    "proposals",
    "review_sessions",
    "trend_evaluations",
    "finding_membership",
    "evidence",
    "observations",
    "findings",
    "canonical_rejections",
    "analysis_generations",
    "otlp_outbox",
    "scan_runs",
    "project_identities",
    "scheduler_leases",
    "source_watermarks",
    "source_schema_snapshots",
)

if len(_DELETE_ORDER) != len(_CLEARED) or frozenset(_DELETE_ORDER) != frozenset(_CLEARED):
    raise RuntimeError("fresh-start delete order must cover every cleared table exactly once")


@dataclass(frozen=True, slots=True)
class FreshStartManifest:
    schema_version: tuple[tuple[int, str, str], ...]
    table_counts: tuple[tuple[str, int], ...]
    stable_ids: tuple[tuple[str, tuple[tuple[Any, ...], ...]], ...]
    project_tuples: tuple[tuple[Any, ...], ...]
    table_hashes: tuple[tuple[str, str], ...]
    approved_source_snapshots: tuple[tuple[Any, ...], ...]
    checksum: str


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=_json_value).encode()
    ).hexdigest()


def _json_value(value: object) -> object:
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    raise TypeError(f"unsupported SQLite value {type(value).__name__}")


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _require_schema(connection: sqlite3.Connection) -> None:
    present = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    missing = tuple(table for table in _TABLES if table not in present)
    if missing:
        raise FreshStartError(f"source schema is not the approved fresh-start schema: {missing!r}")


def _primary_key_columns(connection: sqlite3.Connection, table: str) -> tuple[str, ...]:
    columns = tuple(
        (int(row[5]), str(row[1]))
        for row in connection.execute(f"PRAGMA table_info({_quote(table)})")
        if int(row[5])
    )
    if not columns:
        raise FreshStartError(f"approved table {table!r} has no stable primary key")
    return tuple(name for _, name in sorted(columns))


def _rows(connection: sqlite3.Connection, table: str) -> tuple[tuple[Any, ...], ...]:
    keys = _primary_key_columns(connection, table)
    return tuple(
        tuple(row)
        for row in connection.execute(
            f"SELECT * FROM {_quote(table)} ORDER BY {', '.join(_quote(key) for key in keys)}"
        )
    )


def _manifest_body(manifest: FreshStartManifest) -> dict[str, object]:
    body = asdict(manifest)
    body.pop("checksum")
    return body


def _snapshot(
    connection: sqlite3.Connection,
) -> tuple[
    tuple[tuple[str, int], ...],
    tuple[tuple[str, tuple[tuple[Any, ...], ...]], ...],
    tuple[tuple[str, str], ...],
    tuple[tuple[Any, ...], ...],
]:
    counts: list[tuple[str, int]] = []
    ids: list[tuple[str, tuple[tuple[Any, ...], ...]]] = []
    hashes: list[tuple[str, str]] = []
    for table in _TABLES:
        rows = _rows(connection, table)
        keys = _primary_key_columns(connection, table)
        counts.append((table, len(rows)))
        columns = tuple(
            str(row[1]) for row in connection.execute(f"PRAGMA table_info({_quote(table)})")
        )
        positions = tuple(columns.index(key) for key in keys)
        ids.append((table, tuple(tuple(row[index] for index in positions) for row in rows)))
        hashes.append((table, _digest(rows)))
    approved = tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT id, source, fingerprint, schema_json, captured_at, "
            "approved_at, approved_by FROM source_schema_snapshots "
            "WHERE approved_at IS NOT NULL AND approved_by IS NOT NULL ORDER BY id"
        )
    )
    return tuple(counts), tuple(ids), tuple(hashes), approved


def build_fresh_start_manifest(source_database_path: Path) -> FreshStartManifest:
    """Capture immutable, complete source-state evidence from an approved SQLite schema."""
    source = source_database_path.expanduser().resolve(strict=False)
    if not source.is_file():
        raise FreshStartError(f"source database does not exist: {source}")
    connection = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)
    try:
        _require_schema(connection)
        schema = tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT version, name, checksum FROM migrations ORDER BY version"
            )
        )
        counts, ids, hashes, approved = _snapshot(connection)
        projects = tuple(_rows(connection, "project_identities"))
        provisional = FreshStartManifest(schema, counts, ids, projects, hashes, approved, "")
        return FreshStartManifest(
            schema, counts, ids, projects, hashes, approved, _digest(_manifest_body(provisional))
        )
    finally:
        connection.close()


def _validate_manifest(manifest: FreshStartManifest) -> None:
    if (
        not isinstance(manifest, FreshStartManifest)
        or not manifest.schema_version
        or manifest.checksum != _digest(_manifest_body(manifest))
    ):
        raise FreshStartError("fresh-start manifest checksum is invalid")
    expected_tables = tuple(table for table, _ in manifest.table_counts)
    if (
        expected_tables != _TABLES
        or tuple(table for table, _ in manifest.stable_ids) != _TABLES
        or tuple(table for table, _ in manifest.table_hashes) != _TABLES
    ):
        raise FreshStartError("fresh-start manifest table inventory is invalid")
    if any(
        not isinstance(count, int) or isinstance(count, bool) or count < 0
        for _, count in manifest.table_counts
    ):
        raise FreshStartError("fresh-start manifest counts are invalid")


def _validate_source(connection: sqlite3.Connection, manifest: FreshStartManifest) -> None:
    _require_schema(connection)
    schema = tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT version, name, checksum FROM migrations ORDER BY version"
        )
    )
    counts, ids, hashes, approved = _snapshot(connection)
    if (schema, counts, ids, tuple(_rows(connection, "project_identities")), hashes, approved) != (
        manifest.schema_version,
        manifest.table_counts,
        manifest.stable_ids,
        manifest.project_tuples,
        manifest.table_hashes,
        manifest.approved_source_snapshots,
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


def _delete_guards(connection: sqlite3.Connection) -> tuple[tuple[str, str], ...]:
    guards = tuple(
        (str(row[0]), str(row[1]))
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' "
            "AND tbl_name IN ({}) AND name LIKE '%_no_delete' ORDER BY name".format(
                ",".join("?" for _ in _CLEARED)
            ),
            _CLEARED,
        )
    )
    for name, _ in guards:
        connection.execute(f"DROP TRIGGER {_quote(name)}")
    return guards


def _trigger_set(connection: sqlite3.Connection) -> tuple[tuple[str, str], ...]:
    return tuple(
        (str(row[0]), str(row[1]))
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' ORDER BY name"
        )
    )


def rehearse_fresh_start_copy(
    source: Path, target: Path, manifest: FreshStartManifest, cutoff: str, marker: str
) -> FreshStartManifest:
    """Create and validate a marked, non-live fresh-start database copy."""
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
        source_triggers = _trigger_set(source_connection)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        copy_connection = sqlite3.connect(target_path)
        try:
            source_connection.backup(copy_connection)
        finally:
            copy_connection.close()
        connection = sqlite3.connect(target_path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE")
            guards = _delete_guards(connection)
            for table in _DELETE_ORDER:
                if table == "source_schema_snapshots":
                    connection.execute(
                        "DELETE FROM source_schema_snapshots "
                        "WHERE approved_at IS NULL OR approved_by IS NULL"
                    )
                else:
                    connection.execute(f"DELETE FROM {_quote(table)}")
            connection.execute(
                "INSERT INTO source_watermarks (source, timestamp_ns, row_id, updated_at) "
                "VALUES ('signoz_logs', ?, '', ?) ",
                (cutoff_ns, cutoff),
            )
            for _, sql in guards:
                connection.execute(sql)
            connection.commit()
            _validate_source(source_connection, manifest)
            empty = tuple(
                (
                    table,
                    int(connection.execute(f"SELECT COUNT(*) FROM {_quote(table)}").fetchone()[0]),
                )
                for table in _CLEARED
                if table not in ("source_schema_snapshots", "source_watermarks")
            )
            if any(count for _, count in empty):
                raise FreshStartError("fresh-start copy retains historical populations")
            if connection.execute(
                "SELECT COUNT(*) FROM source_schema_snapshots "
                "WHERE approved_at IS NULL OR approved_by IS NULL"
            ).fetchone() != (0,):
                raise FreshStartError("fresh-start copy retains unapproved source snapshots")
            watermark = connection.execute(
                "SELECT timestamp_ns, row_id FROM source_watermarks WHERE source = 'signoz_logs'"
            ).fetchone()
            if watermark != (cutoff_ns, ""):
                raise FreshStartError("fresh-start copy watermark does not equal cutoff")
            approved = tuple(
                tuple(row)
                for row in connection.execute(
                    "SELECT id, source, fingerprint, schema_json, captured_at, "
                    "approved_at, approved_by FROM source_schema_snapshots "
                    "WHERE approved_at IS NOT NULL AND approved_by IS NOT NULL ORDER BY id"
                )
            )
            if approved != manifest.approved_source_snapshots:
                raise FreshStartError("fresh-start copy did not preserve approved source snapshots")
            if _trigger_set(connection) != source_triggers:
                raise FreshStartError("fresh-start copy trigger schema does not match source")
            if (
                connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]
                or connection.execute("PRAGMA foreign_key_check").fetchall()
            ):
                raise FreshStartError("fresh-start copy failed SQLite integrity verification")
            return manifest
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
    finally:
        source_connection.close()

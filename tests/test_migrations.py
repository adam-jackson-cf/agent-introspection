# ruff: noqa: E501
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_introspection.migrations import MIGRATIONS, MigrationError, apply_migrations


def _connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def test_canonical_migration_creates_current_runtime_schema_without_retired_objects(
    tmp_path: Path,
) -> None:
    path = tmp_path / "introspection.sqlite3"
    connection = _connection(path)
    try:
        applied = apply_migrations(connection, path)
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()

    assert len(MIGRATIONS) == len(applied) == 15
    assert all(migration.backup_path.is_file() for migration in applied)
    assert tables == {
        "canonical_activities",
        "canonical_activity_outbox_evidence",
        "canonical_activity_versions",
        "canonical_finding_membership",
        "canonical_recomputation_schedule",
        "canonical_rejections",
        "evidence",
        "finding_membership",
        "findings",
        "migrations",
        "legacy_attribution_fact_sets",
        "legacy_attribution_delivery_attempts",
        "model_budget_ledger",
        "model_capability_proofs",
        "model_runs",
        "observations",
        "otlp_outbox",
        "project_identities",
        "proposal_drafts",
        "proposal_events",
        "proposals",
        "raw_source_window_anchors",
        "raw_source_window_claims",
        "raw_source_window_completions",
        "review_sessions",
        "scan_runs",
        "scheduler_leases",
        "semantic_classifications",
        "session_context_event_supersessions",
        "session_context_events",
        "session_context_intervals",
        "session_context_replay_mutations",
        "session_context_replay_state",
        "source_session_records",
        "source_session_current",
        "source_session_current_versions",
        "source_session_reconciliation_pending",
        "source_schema_snapshots",
        "source_watermarks",
        "trend_evaluations",
    }


def test_native_source_session_index_backfills_exact_canonical_keys(tmp_path: Path) -> None:
    path = tmp_path / "introspection.sqlite3"
    connection = _connection(path)
    try:
        apply_migrations(connection, path, MIGRATIONS[:14])
        connection.execute(
            """
            INSERT INTO source_session_current (
                source_kind, service_name, source_id, version,
                terminal_outcome, terminal_reason, context_evidence_id,
                project_id, project_name, project_root, project_kind,
                projection_event_id, updated_at, source_timestamp, session_ids_json,
                thread_ids_json, legacy_thread_ids_json, gen_ai_conversation_ids_json
            ) VALUES (
                'log', 'codex_exec', 'source', 1,
                'failed', 'no_authoritative_context', NULL,
                NULL, NULL, NULL, NULL,
                'event', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00',
                '[]', '["thread"]', '[]', '[]'
            )
            """
        )
        connection.commit()
        apply_migrations(connection, path)
        assert connection.execute(
            """
            SELECT native_producer, native_session_id
            FROM source_session_current
            WHERE native_producer = 'codex-cli' AND native_session_id = 'thread'
            """
        ).fetchone() == ("codex-cli", "thread")
    finally:
        connection.close()


def test_canonical_activity_contract_enforces_versions_and_foreign_keys(tmp_path: Path) -> None:
    path = tmp_path / "introspection.sqlite3"
    connection = _connection(path)
    try:
        apply_migrations(connection, path)
        connection.execute(
            "INSERT INTO canonical_activities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "activity",
                "omp",
                "omp",
                "session",
                1,
                2,
                "detector",
                1,
                1,
                "a" * 64,
                '["source"]',
                "tool",
                "file",
                "target",
                "",
                "2026-08-06T00:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO canonical_activity_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "activity",
                1,
                "unresolved",
                None,
                "source",
                None,
                "missing_workspace",
                "2026-08-06T00:00:00+00:00",
            ),
        )
        with pytest.raises(sqlite3.IntegrityError, match="monotonic"):
            connection.execute(
                "INSERT INTO canonical_activity_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "activity",
                    3,
                    "unresolved",
                    None,
                    "source",
                    None,
                    "missing_workspace",
                    "2026-08-06T00:00:00+00:00",
                ),
            )
    finally:
        connection.close()


def test_canonical_migration_rejects_noncanonical_history(tmp_path: Path) -> None:
    path = tmp_path / "introspection.sqlite3"
    connection = _connection(path)
    try:
        connection.execute(
            "CREATE TABLE migrations (version INTEGER PRIMARY KEY, name TEXT, checksum TEXT, applied_at TEXT)"
        )
        connection.execute(
            "INSERT INTO migrations VALUES (1, 'historical', 'x' || printf('%063d', 0), 'now')"
        )
        connection.commit()
        connection.execute("PRAGMA user_version = 1")
        with pytest.raises(MigrationError, match="does not match canonical history"):
            apply_migrations(connection, path)
    finally:
        connection.close()

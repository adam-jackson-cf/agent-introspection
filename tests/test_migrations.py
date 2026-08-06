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

    assert len(MIGRATIONS) == len(applied) == 4
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
        "model_budget_ledger",
        "model_capability_proofs",
        "model_runs",
        "observations",
        "otlp_outbox",
        "project_identities",
        "proposal_drafts",
        "proposal_events",
        "proposals",
        "review_sessions",
        "scan_runs",
        "scheduler_leases",
        "semantic_classifications",
        "session_context_events",
        "session_context_intervals",
        "session_context_replay_mutations",
        "session_context_replay_state",
        "source_schema_snapshots",
        "source_watermarks",
        "trend_evaluations",
    }


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

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_introspection.migrations import (
    MIGRATIONS,
    Migration,
    MigrationError,
    apply_migrations,
)


def _connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def test_initial_migration_creates_every_plan_table_and_verified_backup(
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
    finally:
        connection.close()

    assert tables == {
        "migrations",
        "scan_runs",
        "source_schema_snapshots",
        "source_watermarks",
        "project_identities",
        "project_aliases",
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
    }
    assert len(applied) == 2
    assert applied[0].backup_path.is_file()
    backup = sqlite3.connect(f"{applied[0].backup_path.as_uri()}?mode=ro", uri=True)
    try:
        assert backup.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert (
            backup.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall() == []
        )
    finally:
        backup.close()


def test_schema_matches_review_and_capability_consumers(tmp_path: Path) -> None:
    path = tmp_path / "introspection.sqlite3"
    connection = _connection(path)
    try:
        apply_migrations(connection, path)
        review_columns = {
            str(row[1]): str(row[2])
            for row in connection.execute("PRAGMA table_info(review_sessions)")
        }
        proof_columns = {
            str(row[1]): str(row[2])
            for row in connection.execute("PRAGMA table_info(model_capability_proofs)")
        }
        proof_indexes = {
            str(row[1]): int(row[2])
            for row in connection.execute("PRAGMA index_list(model_capability_proofs)")
        }
    finally:
        connection.close()

    assert review_columns["batch_id"] == "TEXT"
    assert review_columns["schema_version"] == "INTEGER"
    assert proof_columns["schema_version"] == "INTEGER"
    assert proof_indexes["model_capability_proofs_lookup_idx"] == 0


def test_model_capability_proofs_are_append_only_not_unique(tmp_path: Path) -> None:
    path = tmp_path / "introspection.sqlite3"
    connection = _connection(path)
    apply_migrations(connection, path)
    values = (
        "gpt-5.5",
        "high",
        "thread",
        "trace",
        1,
        100,
        "tool-version",
        "f" * 64,
        "2026-07-10T10:00:00+00:00",
        "2026-08-09T10:00:00+00:00",
    )
    statement = """
        INSERT INTO model_capability_proofs (
            id, model, effort, thread_id, trace_id, schema_version, total_tokens,
            tool_version, schema_fingerprint, proven_at, expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    try:
        connection.execute(statement, ("proof-1", *values))
        connection.execute(statement, ("proof-2", *values))
        connection.commit()
        assert connection.execute("SELECT COUNT(*) FROM model_capability_proofs").fetchone()[0] == 2
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE model_capability_proofs SET total_tokens = 101 WHERE id = 'proof-1'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM model_capability_proofs WHERE id = 'proof-1'")
    finally:
        connection.close()


def test_duplicate_otlp_event_id_requires_the_identical_immutable_payload(
    tmp_path: Path,
) -> None:
    path = tmp_path / "introspection.sqlite3"
    connection = _connection(path)
    apply_migrations(connection, path)
    statement = """
        INSERT INTO otlp_outbox (
            event_id, payload_json, status, attempt_count, next_attempt_at, created_at
        ) VALUES ('event-1', ?, 'pending', 0, '2026-07-10', '2026-07-10')
        ON CONFLICT(event_id) DO NOTHING
    """
    try:
        connection.execute(statement, ('{"value":1}',))
        connection.execute(statement, ('{"value":1}',))
        with pytest.raises(sqlite3.IntegrityError, match="conflicts"):
            connection.execute(statement, ('{"value":2}',))
    finally:
        connection.close()


def test_migration_history_and_user_version_must_match(tmp_path: Path) -> None:
    path = tmp_path / "introspection.sqlite3"
    connection = _connection(path)
    try:
        apply_migrations(connection, path)
        connection.execute("PRAGMA user_version = 0")
        with pytest.raises(MigrationError, match="does not match migration history"):
            apply_migrations(connection, path)
    finally:
        connection.close()


def test_failed_migration_rolls_back_every_statement(tmp_path: Path) -> None:
    path = tmp_path / "introspection.sqlite3"
    connection = _connection(path)
    broken = Migration(
        1,
        "broken",
        (
            """
            CREATE TABLE migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL
            ) STRICT
            """,
            "CREATE TABLE should_not_survive (id INTEGER PRIMARY KEY) STRICT",
            "CREATE TABLE invalid SQL",
        ),
    )
    try:
        with pytest.raises(MigrationError, match="migration 1 failed"):
            apply_migrations(connection, path, (broken,))
        assert (
            connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            == []
        )
    finally:
        connection.close()


def test_migration_numbers_are_contiguous(tmp_path: Path) -> None:
    path = tmp_path / "introspection.sqlite3"
    connection = _connection(path)
    try:
        with pytest.raises(MigrationError, match="contiguous"):
            apply_migrations(connection, path, (Migration(2, "gap", ("SELECT 1",)),))
    finally:
        connection.close()


def test_canonical_migration_checksum_is_stable() -> None:
    assert len(MIGRATIONS[0].checksum) == 64


def test_findings_rebuild_preserves_dependents_and_permits_zero_window_counts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "introspection.sqlite3"
    connection = _connection(path)
    try:
        apply_migrations(connection, path, MIGRATIONS[:1])
        connection.execute(
            "INSERT INTO scan_runs (id, status, started_at) VALUES ('scan-1', 'succeeded', 'now')"
        )
        connection.execute(
            """
            INSERT INTO observations (
                id, scan_run_id, detector_id, detector_version, category, project_identity_id,
                task_identity, turn_identity, occurred_at_ns, fingerprint, operation_kind,
                target_kind, normalized_target, normalized_failure_class, normalization_version,
                membership_explanation, attributes_json, created_at
            ) VALUES (
                'observation-1', 'scan-1', 'detector', 1, 'category', NULL, 'thread:one', NULL,
                1, ?, 'event', 'none', 'operation', 'failure', 1, 'membership', '{}', 'now'
            )
            """,
            ("a" * 64,),
        )
        connection.execute(
            """
            INSERT INTO findings (
                id, fingerprint, category, project_identity_id, trend_state, detector_id,
                detector_version, first_seen_ns, last_seen_ns, occurrence_count,
                canonical_task_count, local_day_count, entity_version, updated_at
            ) VALUES (
                'finding-1', ?, 'category', NULL, 'actionable', 'detector', 1, 1, 1, 1, 1, 1,
                1, 'now'
            )
            """,
            ("b" * 64,),
        )
        connection.execute(
            """
            INSERT INTO finding_membership (finding_id, observation_id, rationale, created_at)
            VALUES ('finding-1', 'observation-1', 'membership', 'now')
            """
        )
        connection.execute(
            """
            INSERT INTO trend_evaluations (
                id, finding_id, trend_state, window_start, window_end, occurrence_count,
                canonical_task_count, local_day_count, rationale, created_at
            ) VALUES (
                'evaluation-1', 'finding-1', 'actionable', 'start', 'end', 1, 1, 1, 'rule', 'now'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO proposals (
                id, finding_id, state, payload_json, created_at, updated_at, entity_version
            ) VALUES ('proposal-1', 'finding-1', 'pending', '{}', 'now', 'now', 1)
            """
        )
        connection.commit()

        applied = apply_migrations(connection, path)

        assert [migration.version for migration in applied] == [2]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT COUNT(*) FROM finding_membership").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM trend_evaluations").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 1
        connection.execute(
            """
            UPDATE findings
            SET trend_state = 'dormant', occurrence_count = 0, canonical_task_count = 0,
                local_day_count = 0, entity_version = 2
            WHERE id = 'finding-1'
            """
        )
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            connection.execute("DELETE FROM findings WHERE id = 'finding-1'")
    finally:
        connection.close()

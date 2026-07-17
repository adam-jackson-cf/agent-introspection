from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_introspection.database import (
    DatabaseError,
    ObservationRecord,
    SourceWatermark,
    backup_database,
    connect_database,
    integrity_check,
    manual_vacuum,
    persist_observations_and_watermark,
    quick_check,
    restore_database,
    verify_database_file,
    weekly_maintenance,
)


def _scan(connection: sqlite3.Connection, scan_id: str = "scan-1") -> None:
    connection.execute(
        """
        INSERT INTO scan_runs (id, status, started_at)
        VALUES (?, 'running', '2026-07-10T10:00:00+00:00')
        """,
        (scan_id,),
    )
    connection.commit()


def _observation(
    observation_id: str,
    *,
    scan_id: str = "scan-1",
    category: str = "tool_failure",
    fingerprint: str = "a" * 64,
) -> ObservationRecord:
    return ObservationRecord(
        id=observation_id,
        scan_run_id=scan_id,
        detector_id="tool_failure",
        detector_version=1,
        category=category,
        project_identity_id=None,
        task_identity="thread:one",
        turn_identity="turn:one",
        occurred_at_ns=1_000,
        fingerprint=fingerprint,
        operation_kind="shell",
        target_kind="path",
        normalized_target="src/app.py",
        normalized_failure_class="exit_1",
        normalization_version=1,
        membership_explanation="explicit failed tool result",
        attributes={"event_ids": ["event-1"]},
        created_at="2026-07-10T10:00:01+00:00",
    )


def _watermark(timestamp_ns: int, row_id: str = "row-1") -> SourceWatermark:
    return SourceWatermark(
        source="signoz_logs",
        timestamp_ns=timestamp_ns,
        row_id=row_id,
        updated_at="2026-07-10T10:00:02+00:00",
    )


def test_connection_enforces_wal_foreign_keys_timeout_and_schema(tmp_path: Path) -> None:
    path = tmp_path / "state" / "introspection.sqlite3"
    connection = connect_database(path, busy_timeout_ms=12_345)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 12_345
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert quick_check(connection) == ("ok",)
        assert integrity_check(connection) == ("ok",)
    finally:
        connection.close()


def test_observations_and_watermark_commit_atomically_and_replay_idempotently(
    tmp_path: Path,
) -> None:
    connection = connect_database(tmp_path / "introspection.sqlite3")
    try:
        _scan(connection)
        observation = _observation("observation-1")
        persist_observations_and_watermark(connection, [observation], _watermark(1_000))
        persist_observations_and_watermark(connection, [observation], _watermark(1_000))

        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
        assert connection.execute(
            "SELECT timestamp_ns, row_id FROM source_watermarks WHERE source = 'signoz_logs'"
        ).fetchone() == (1_000, "row-1")
    finally:
        connection.close()


def test_observation_failure_rolls_back_rows_and_watermark(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "introspection.sqlite3")
    try:
        _scan(connection)
        with pytest.raises(sqlite3.IntegrityError):
            persist_observations_and_watermark(
                connection,
                [_observation("valid"), _observation("invalid", fingerprint="short")],
                _watermark(2_000),
            )
        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM source_watermarks").fetchone()[0] == 0
    finally:
        connection.close()


def test_conflicting_replay_and_watermark_regression_fail_closed(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "introspection.sqlite3")
    try:
        _scan(connection)
        persist_observations_and_watermark(
            connection, [_observation("observation-1")], _watermark(2_000, "row-2")
        )
        with pytest.raises(DatabaseError, match="conflicts"):
            persist_observations_and_watermark(
                connection,
                [_observation("observation-1", category="changed")],
                _watermark(3_000, "row-3"),
            )
        with pytest.raises(DatabaseError, match="backwards"):
            persist_observations_and_watermark(connection, [], _watermark(1_999, "row-9"))
        assert connection.execute(
            "SELECT timestamp_ns, row_id FROM source_watermarks"
        ).fetchone() == (2_000, "row-2")
    finally:
        connection.close()


def test_online_backup_and_restore_are_verified_and_preserve_safety_copy(
    tmp_path: Path,
) -> None:
    path = tmp_path / "introspection.sqlite3"
    connection = connect_database(path)
    _scan(connection)
    backup_path = backup_database(connection, tmp_path / "backups" / "known-good.sqlite3")
    connection.execute(
        "UPDATE scan_runs SET status = 'succeeded', completed_at = ? WHERE id = 'scan-1'",
        ("2026-07-10T10:05:00+00:00",),
    )
    connection.commit()
    connection.close()

    result = restore_database(path, backup_path)

    assert result.safety_backup_path is not None
    assert verify_database_file(result.database_path) == ("ok",)
    restored = connect_database(path)
    safety = sqlite3.connect(result.safety_backup_path)
    try:
        assert restored.execute("SELECT status FROM scan_runs").fetchone()[0] == "running"
        assert safety.execute("SELECT status FROM scan_runs").fetchone()[0] == "succeeded"
    finally:
        restored.close()
        safety.close()


def test_corrupt_restore_source_leaves_target_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "introspection.sqlite3"
    connection = connect_database(path)
    _scan(connection)
    connection.close()
    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"not sqlite")

    with pytest.raises(DatabaseError):
        restore_database(path, corrupt)

    current = connect_database(path)
    try:
        assert current.execute("SELECT id FROM scan_runs").fetchone()[0] == "scan-1"
    finally:
        current.close()


def test_weekly_maintenance_runs_integrity_analyze_and_online_backup(tmp_path: Path) -> None:
    path = tmp_path / "introspection.sqlite3"
    connection = connect_database(path)
    try:
        result = weekly_maintenance(connection, path, backup_directory=tmp_path / "weekly-backups")
        assert result.integrity_result == ("ok",)
        assert result.backup_path.is_file()
        assert verify_database_file(result.backup_path) == ("ok",)
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_stat1'"
        ).fetchone() == (1,)
    finally:
        connection.close()


def test_manual_vacuum_requires_more_than_25_percent_free_pages_and_backup(
    tmp_path: Path,
) -> None:
    path = tmp_path / "introspection.sqlite3"
    connection = connect_database(path)
    try:
        not_needed = manual_vacuum(connection, path, backup_directory=tmp_path / "backups")
        assert not not_needed.vacuumed
        assert not_needed.backup_path is None

        connection.execute("CREATE TABLE disposable (payload BLOB NOT NULL)")
        connection.executemany(
            "INSERT INTO disposable(payload) VALUES (zeroblob(4096))", [()] * 200
        )
        connection.commit()
        connection.execute("DROP TABLE disposable")
        connection.commit()

        compacted = manual_vacuum(connection, path, backup_directory=tmp_path / "backups")
        assert compacted.free_page_ratio > 0.25
        assert compacted.vacuumed
        assert compacted.backup_path is not None
        assert verify_database_file(compacted.backup_path) == ("ok",)
    finally:
        connection.close()

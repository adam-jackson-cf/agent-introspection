from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from agent_introspection.database import (
    CanonicalActivity,
    CanonicalAttribution,
    CanonicalSourceMembership,
    DatabaseError,
    ObservationRecord,
    SourceWatermark,
    backup_database,
    connect_database,
    integrity_check,
    manual_vacuum,
    migrate_observations_to_canonical_activities,
    persist_canonical_activity,
    persist_observations_and_watermark,
    quick_check,
    restore_database,
    verify_database_file,
    weekly_maintenance,
)
from agent_introspection.migrations import MIGRATIONS


def _scan(connection: sqlite3.Connection, scan_id: str = "scan-1") -> None:
    connection.execute(
        """
        INSERT INTO scan_runs (id, status, started_at)
        VALUES (?, 'running', '2026-07-10T10:00:00+00:00')
        """,
        (scan_id,),
    )
    connection.commit()


def _evidence(
    connection: sqlite3.Connection,
    observation_id: str,
    *,
    evidence_id: str = "evidence-1",
    evidence_kind: str = "hydrated_log",
    source_reference: str = "signoz-log:log-1",
) -> None:
    connection.execute(
        """
        INSERT INTO evidence (
            id, observation_id, evidence_kind, source_reference, redacted_content,
            content_hash, correlation_status, created_at
        ) VALUES (?, ?, ?, ?, NULL, ?, 'correlated', '2026-07-10T10:00:01+00:00')
        """,
        (
            evidence_id,
            observation_id,
            evidence_kind,
            source_reference,
            "e" * 64,
        ),
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
        attributes={
            "attribution.method": "source",
            "correlation_id": "thread-1",
            "event_ids": ["event-1"],
            "producer": "codex-cli",
            "producer_surface": "codex-cli",
        },
        created_at="2026-07-10T10:00:01+00:00",
    )


def _watermark(timestamp_ns: int, row_id: str = "row-1") -> SourceWatermark:
    return SourceWatermark(
        source="signoz_logs",
        timestamp_ns=timestamp_ns,
        row_id=row_id,
        updated_at="2026-07-10T10:00:02+00:00",
    )


def _canonical_activity(
    *,
    membership: CanonicalSourceMembership | None = None,
    operation_kind: str = "shell",
) -> CanonicalActivity:
    return CanonicalActivity(
        producer="codex-cli",
        producer_surface="codex-cli",
        correlation_id="thread-1",
        source_started_at_ns=1_000,
        source_ended_at_ns=2_000,
        detector_id="tool_failure",
        detector_version=1,
        normalization_version=1,
        source_membership=membership
        or CanonicalSourceMembership(event_ids=("event-2", "event-1"), log_ids=("log-1",)),
        operation_kind=operation_kind,
        target_kind="path",
        normalized_target="src/app.py",
        normalized_failure_class="exit_1",
        created_at="2026-08-05T00:00:00+00:00",
    )


@pytest.mark.parametrize(
    ("producer", "producer_surface", "valid"),
    (
        ("codex-cli", "codex-cli", True),
        ("codex-app-server", "codex-app", True),
        ("codex-app-server", "codex-app-server", True),
        ("omp", "omp", True),
        ("claude-code", "claude-code", True),
        ("codex-cli", "cli", False),
        ("cli", "cli", False),
        ("codex-cli", "codex-app", False),
        ("codex-app-server", "codex-cli", False),
        ("omp", "claude-code", False),
        ("claude-code", "omp", False),
    ),
)
def test_canonical_activity_accepts_only_exact_producer_surface_pairs(
    producer: str, producer_surface: str, valid: bool
) -> None:
    if valid:
        activity = replace(
            _canonical_activity(),
            producer=producer,
            producer_surface=producer_surface,
        )
        assert (activity.producer, activity.producer_surface) == (
            producer,
            producer_surface,
        )
    else:
        with pytest.raises(ValueError, match="canonical activity fields are invalid"):
            replace(
                _canonical_activity(),
                producer=producer,
                producer_surface=producer_surface,
            )


def _unresolved_attribution(reason_code: str = "missing_workspace") -> CanonicalAttribution:
    return CanonicalAttribution(
        state="unresolved",
        project_identity_id=None,
        method="source",
        evidence_id=None,
        reason_code=reason_code,
        created_at="2026-08-05T00:00:00+00:00",
    )


def test_canonical_activity_writer_has_deterministic_membership_and_versions(
    tmp_path: Path,
) -> None:
    connection = connect_database(tmp_path / "introspection.sqlite3")
    try:
        ordered = _canonical_activity(
            membership=CanonicalSourceMembership(
                event_ids=("event-1", "event-2"),
                log_ids=("log-1",),
            )
        )
        reordered = _canonical_activity(
            membership=CanonicalSourceMembership(
                event_ids=("event-2", "event-1"),
                log_ids=("log-1",),
            )
        )
        assert ordered.id == reordered.id
        assert reordered.source_membership.event_ids == ("event-1", "event-2")

        with connection:
            first = persist_canonical_activity(connection, ordered, _unresolved_attribution())
            replay = persist_canonical_activity(connection, reordered, _unresolved_attribution())
        assert first.version == replay.version == 1
        assert first.version_inserted
        assert not replay.version_inserted

        with connection:
            changed = persist_canonical_activity(
                connection,
                ordered,
                _unresolved_attribution("invalid_workspace"),
            )
        assert changed.version == 2
        assert changed.version_inserted
        assert connection.execute(
            """
            SELECT version FROM canonical_activity_versions
            WHERE activity_id = ? ORDER BY version
            """,
            (ordered.id,),
        ).fetchall() == [(1,), (2,)]

        with pytest.raises(DatabaseError, match="membership hash conflicts"):
            persist_canonical_activity(
                connection,
                _canonical_activity(operation_kind="editor"),
                _unresolved_attribution(),
            )
    finally:
        connection.close()


def test_observation_migration_creates_an_immutable_complete_manifest(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "introspection.sqlite3")
    try:
        _scan(connection)
        observation = _observation("observation-1")
        persist_observations_and_watermark(connection, (observation,), _watermark(1_000))
        _evidence(connection, observation.id)
        _evidence(
            connection,
            observation.id,
            evidence_id="evidence-2",
            evidence_kind="source_reference",
            source_reference="signoz-log:trace:0123456789abcdef0123456789abcdef",
        )

        result = migrate_observations_to_canonical_activities(
            connection, migrated_at="2026-08-05T00:00:00+00:00"
        )

        assert result.observation_ids == ("observation-1",)
        assert result.activity_ids == (
            CanonicalActivity(
                producer="codex-cli",
                producer_surface="codex-cli",
                correlation_id="thread-1",
                source_started_at_ns=1_000,
                source_ended_at_ns=1_000,
                detector_id="tool_failure",
                detector_version=1,
                normalization_version=1,
                source_membership=CanonicalSourceMembership(
                    event_ids=("event-1",),
                    log_ids=("log-1",),
                    span_ids=("trace:0123456789abcdef0123456789abcdef",),
                ),
                operation_kind="shell",
                target_kind="path",
                normalized_target="src/app.py",
                normalized_failure_class="exit_1",
                created_at="2026-07-10T10:00:01+00:00",
            ).id,
        )
        assert len(result.mapping_hash) == 64
        expected_membership = CanonicalSourceMembership(
            event_ids=("event-1",),
            log_ids=("log-1",),
            span_ids=("trace:0123456789abcdef0123456789abcdef",),
        )
        assert connection.execute(
            """
            SELECT id, source_membership_hash, source_membership_json
            FROM canonical_activities
            """
        ).fetchall() == [
            (result.activity_ids[0], expected_membership.hash, expected_membership.json)
        ]
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM observation_activity_migration_manifest"
            ).fetchone()[0]
            == 1
        )
        assert (
            migrate_observations_to_canonical_activities(
                connection, migrated_at="2026-08-05T00:00:00+00:00"
            )
            == result
        )

        with pytest.raises(sqlite3.IntegrityError, match="manifest is immutable"):
            connection.execute(
                "UPDATE observation_activity_migration_manifest SET migrated_at = 'changed'"
            )
    finally:
        connection.close()


def test_observation_migration_rejects_ambiguous_activity_collisions(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "introspection.sqlite3")
    try:
        _scan(connection)
        first = _observation("observation-1")
        second = replace(first, id="observation-2")
        persist_observations_and_watermark(connection, (first, second), _watermark(1_000))
        _evidence(connection, first.id, evidence_id="evidence-1")
        _evidence(connection, second.id, evidence_id="evidence-2")

        with pytest.raises(DatabaseError, match="ambiguous canonical activity collisions"):
            migrate_observations_to_canonical_activities(
                connection, migrated_at="2026-08-05T00:00:00+00:00"
            )
        assert connection.execute("SELECT COUNT(*) FROM canonical_activities").fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("event_ids", "evidence_kind", "source_reference", "error"),
    (
        (["event-1"], "unrecognized_kind", "signoz-log:log-1", "unrecognized evidence kind"),
        (
            ["event-1"],
            "hydrated_log",
            "other-log:log-1",
            "unrecognized evidence source reference",
        ),
        (
            ["event-1"],
            "source_reference",
            "other-log:log-1",
            "unrecognized evidence source reference",
        ),
        ([""], "hydrated_log", "signoz-log:log-1", "invalid event_ids"),
        (["event-1"], "hydrated_log", "signoz-log:", "invalid source identifier"),
        (
            ["event-1"],
            "source_reference",
            "signoz-log:trace:not-hex",
            "invalid source identifier",
        ),
        ([], None, None, "no source membership"),
    ),
)
def test_observation_migration_rejects_invalid_legacy_source_evidence(
    tmp_path: Path,
    event_ids: list[str],
    evidence_kind: str | None,
    source_reference: str | None,
    error: str,
) -> None:
    connection = connect_database(tmp_path / "introspection.sqlite3")
    try:
        _scan(connection)
        base = _observation("observation-1")
        observation = replace(
            base,
            attributes={**base.attributes, "event_ids": event_ids},
        )
        persist_observations_and_watermark(connection, (observation,), _watermark(1_000))
        if evidence_kind is not None and source_reference is not None:
            _evidence(
                connection,
                observation.id,
                evidence_kind=evidence_kind,
                source_reference=source_reference,
            )

        with pytest.raises(DatabaseError, match=error):
            migrate_observations_to_canonical_activities(
                connection, migrated_at="2026-08-05T00:00:00+00:00"
            )
        assert connection.execute("SELECT COUNT(*) FROM canonical_activities").fetchone()[0] == 0
    finally:
        connection.close()


def test_connection_enforces_wal_foreign_keys_timeout_and_schema(tmp_path: Path) -> None:
    path = tmp_path / "state" / "introspection.sqlite3"
    connection = connect_database(path, busy_timeout_ms=12_345)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 12_345
        assert connection.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
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

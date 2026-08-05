from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_introspection.migrations import (
    CANONICAL_CUTOVER_MANIFEST,
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
        project_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(project_identities)")
        }
        evidence_columns = {
            str(row[1]): str(row[2]) for row in connection.execute("PRAGMA table_info(evidence)")
        }
    finally:
        connection.close()

    assert tables == {
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
    }
    assert evidence_columns == {
        "id": "TEXT",
        "observation_id": "TEXT",
        "evidence_kind": "TEXT",
        "source_reference": "TEXT",
        "redacted_content": "TEXT",
        "content_hash": "TEXT",
        "correlation_status": "TEXT",
        "created_at": "TEXT",
    }
    manifest_surface = {
        (entry.object_kind, entry.object_name) for entry in CANONICAL_CUTOVER_MANIFEST
    }
    assert {
        "table",
        "column",
        "index",
        "trigger",
        "foreign_key",
        "command",
        "asset",
        "test",
    } <= {entry.object_kind for entry in CANONICAL_CUTOVER_MANIFEST}
    assert ("table", "observations") in manifest_surface
    assert ("table", "analysis_generations") in manifest_surface
    assert len(applied) == len(MIGRATIONS)
    assert applied[0].backup_path.is_file()
    assert "canonical_name" in project_columns
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
    assert review_columns["purpose"] == "TEXT"
    assert review_columns["entity_version"] == "INTEGER"
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


def test_canonical_activity_ledger_enforces_versions_recomputation_and_rejections(
    tmp_path: Path,
) -> None:
    path = tmp_path / "introspection.sqlite3"
    connection = _connection(path)
    activity = (
        "activity-1",
        "omp",
        "omp",
        "session-1",
        100,
        200,
        "detector",
        1,
        1,
        "a" * 64,
        '["event-1"]',
        "tool",
        "file",
        "target",
        "",
        "2026-08-05T00:00:00+00:00",
    )
    try:
        apply_migrations(connection, path)
        canonical_columns = {
            str(row[1]): str(row[2])
            for row in connection.execute("PRAGMA table_info(canonical_activities)")
        }
        version_columns = {
            str(row[1]): str(row[2])
            for row in connection.execute("PRAGMA table_info(canonical_activity_versions)")
        }
        activity_indexes = {
            str(row[1]) for row in connection.execute("PRAGMA index_list(canonical_activities)")
        }
        version_foreign_keys = {
            (str(row[2]), str(row[3]), str(row[4]))
            for row in connection.execute("PRAGMA foreign_key_list(canonical_activity_versions)")
        }
        indexes = {
            str(row[1])
            for row in connection.execute("PRAGMA index_list(canonical_activity_versions)")
        }
        connection.execute(
            """
            INSERT INTO canonical_activities (
                id, producer, producer_surface, correlation_id, source_started_at_ns,
                source_ended_at_ns, detector_id, detector_version, normalization_version,
                source_membership_hash, source_membership_json, operation_kind, target_kind,
                normalized_target, normalized_failure_class, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            activity,
        )
        connection.execute(
            """
            INSERT INTO canonical_activity_versions (
                activity_id, version, attribution_state, project_identity_id,
                attribution_method, attribution_evidence_id, reason_code, created_at
            ) VALUES ('activity-1', 1, 'unresolved', NULL, 'source', NULL,
                      'missing_workspace', '2026-08-05T00:00:00+00:00')
            """
        )
        with pytest.raises(sqlite3.IntegrityError, match="monotonic"):
            connection.execute(
                """
                INSERT INTO canonical_activity_versions (
                    activity_id, version, attribution_state, project_identity_id,
                    attribution_method, attribution_evidence_id, reason_code, created_at
                ) VALUES ('activity-1', 3, 'unresolved', NULL, 'source', NULL,
                          'missing_workspace', '2026-08-05T00:00:00+00:00')
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO canonical_activity_versions (
                    activity_id, version, attribution_state, project_identity_id,
                    attribution_method, attribution_evidence_id, reason_code, created_at
                ) VALUES ('activity-1', 2, 'resolved', NULL, 'source', NULL, NULL,
                          '2026-08-05T00:00:00+00:00')
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO canonical_activities (
                    id, producer, producer_surface, correlation_id, source_started_at_ns,
                    source_ended_at_ns, detector_id, detector_version, normalization_version,
                    source_membership_hash, source_membership_json, operation_kind, target_kind,
                    normalized_target, normalized_failure_class, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("activity-2", *activity[1:]),
            )
        connection.execute(
            """
            INSERT INTO canonical_recomputation_schedule (
                activity_id, activity_version, aggregate_kind, scheduled_at, completed_at
            ) VALUES ('activity-1', 1, 'findings', '2026-08-05T00:00:00+00:00', NULL)
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO canonical_recomputation_schedule (
                    activity_id, activity_version, aggregate_kind, scheduled_at, completed_at
                ) VALUES ('activity-1', 2, 'findings', '2026-08-05T00:00:00+00:00', NULL)
                """
            )
        connection.execute(
            """
            INSERT INTO canonical_rejections (
                id, producer, producer_surface, correlation_id, lifecycle_event, occurred_at,
                reason_code, source_adapter, created_at
            ) VALUES (
                'rejection-1', 'omp', 'omp', NULL, 'session_start', '2026-08-05T00:00:00+00:00',
                'missing_correlation_id', 'omp-native', '2026-08-05T00:00:00+00:00'
            )
            """
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE canonical_rejections SET source_adapter = 'other'")
        assert canonical_columns["source_membership_json"] == "TEXT"
        assert version_columns["activity_id"] == "TEXT"
        assert ("canonical_activities", "activity_id", "id") in version_foreign_keys
        manifest_columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(observation_activity_migration_manifest)"
            )
        }
        finding_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(findings)")}
        membership_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(canonical_finding_membership)")
        }
        assert "canonical_activities_source_membership_idx" in activity_indexes
        assert "canonical_activity_versions_latest_idx" in indexes
        assert manifest_columns == {
            "observation_id",
            "activity_id",
            "source_membership_hash",
            "mapping_hash",
            "migrated_at",
        }
        assert {"is_active", "replaced_by_finding_id"} <= finding_columns
        assert membership_columns == {"finding_id", "activity_id", "rationale", "created_at"}
        assert connection.execute(
            "SELECT source_membership_json FROM canonical_activities WHERE id = 'activity-1'"
        ).fetchone() == ('["event-1"]',)
    finally:
        connection.close()


def test_canonical_activity_outbox_evidence_is_deterministic_and_rejections_are_bounded(
    tmp_path: Path,
) -> None:
    path = tmp_path / "introspection.sqlite3"
    connection = _connection(path)
    try:
        apply_migrations(connection, path)
        rejection_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(canonical_rejections)")
        }
        evidence_indexes = {
            str(row[1]): int(row[2])
            for row in connection.execute("PRAGMA index_list(canonical_activity_outbox_evidence)")
        }
        event_id_unique = any(
            unique
            and {str(row[2]) for row in connection.execute(f"PRAGMA index_info({index_name})")}
            == {"event_id"}
            for index_name, unique in evidence_indexes.items()
        )
    finally:
        connection.close()

    assert rejection_columns == {
        "id",
        "producer",
        "producer_surface",
        "correlation_id",
        "lifecycle_event",
        "occurred_at",
        "reason_code",
        "source_adapter",
        "created_at",
    }
    assert event_id_unique


def test_canonical_migration_rehearsal_preserves_preexisting_rows(tmp_path: Path) -> None:
    path = tmp_path / "introspection.sqlite3"
    connection = _connection(path)
    try:
        apply_migrations(connection, path, MIGRATIONS[:12])
        connection.execute(
            """
            INSERT INTO project_identities (
                id, identity_kind, canonical_path, git_common_dir, created_at, canonical_name
            ) VALUES ('project-1', 'git', '/project', '/project/.git', '2026-08-05', 'project')
            """
        )
        connection.commit()
        applied = apply_migrations(connection, path)
        assert [migration.version for migration in applied] == [13, 14]
        assert connection.execute("SELECT COUNT(*) FROM canonical_activities").fetchone() == (0,)
        assert connection.execute("PRAGMA user_version").fetchone() == (14,)
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

        assert [migration.version for migration in applied] == [
            2,
            3,
            4,
            5,
            6,
            7,
            8,
            9,
            10,
            11,
            12,
            13,
            14,
        ]
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


def test_review_lifecycle_telemetry_removal_preserves_sessions_and_installs_guards(
    tmp_path: Path,
) -> None:
    path = tmp_path / "introspection.sqlite3"
    connection = _connection(path)
    try:
        apply_migrations(connection, path, MIGRATIONS[:3])
        connection.execute(
            """
            INSERT INTO review_sessions (
                id, batch_id, nonce, schema_version, kind, requested_model, requested_effort,
                ordered_candidate_ids_json, payload_hash, byte_count, reserved_model_budget,
                status, created_at, imported_at
            ) VALUES (
                'imported-session', 'batch', 'nonce-imported', 1, 'classification', 'requested',
                'standard', '[\"candidate\"]', ?, 1, 10, 'imported', 'then', 'now'
            )
            """,
            ("a" * 64,),
        )
        connection.execute(
            """
            INSERT INTO review_sessions (
                id, batch_id, nonce, schema_version, kind, requested_model, requested_effort,
                ordered_candidate_ids_json, payload_hash, byte_count, reserved_model_budget,
                status, created_at, imported_at
            ) VALUES (
                'exported-session', 'batch', 'nonce-exported', 1, 'proposal', 'requested',
                'standard', '[\"candidate\"]', ?, 1, 10, 'exported', 'then', NULL
            )
            """,
            ("b" * 64,),
        )
        connection.commit()

        connection.execute(
            """
            INSERT INTO otlp_outbox (
                event_id, payload_json, status, attempt_count, next_attempt_at, created_at
            ) VALUES ('retired-review-event', ?, 'delivered', 1, 'then', 'then')
            """,
            ('{"event.name":"introspection.review.activity_snapshot"}',),
        )
        connection.execute(
            """
            INSERT INTO otlp_outbox (
                event_id, payload_json, status, attempt_count, next_attempt_at, created_at
            ) VALUES ('retained-event', ?, 'delivered', 1, 'then', 'then')
            """,
            ('{"event.name":"introspection.pipeline.snapshot"}',),
        )
        connection.commit()

        applied = apply_migrations(connection, path)

        assert [migration.version for migration in applied] == [
            4,
            5,
            6,
            7,
            8,
            9,
            10,
            11,
            12,
            13,
            14,
        ]
        assert connection.execute(
            "SELECT id, purpose, status, entity_version FROM review_sessions ORDER BY id"
        ).fetchall() == [
            ("exported-session", "proposal", "exported", 1),
            ("imported-session", "classification", "imported", 2),
        ]
        removed_tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name IN ('review_session_events', 'review_activity_snapshots')"
            )
        }
        assert removed_tables == set()
        assert connection.execute(
            "SELECT event_id FROM otlp_outbox ORDER BY event_id"
        ).fetchall() == [("retained-event",)]
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE review_sessions SET purpose = 'proposal' WHERE id = 'imported-session'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            connection.execute("DELETE FROM review_sessions WHERE id = 'imported-session'")
    finally:
        connection.close()

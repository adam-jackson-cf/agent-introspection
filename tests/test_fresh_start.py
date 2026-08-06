from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import agent_introspection.fresh_start as fresh_start
from agent_introspection.fresh_start import (
    FreshStartError,
    FreshStartManifest,
    build_fresh_start_manifest,
    rehearse_fresh_start_copy,
)
from agent_introspection.migrations import apply_migrations


def _source(path: Path) -> Path:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    apply_migrations(connection, path)
    try:
        connection.execute(
            "INSERT INTO source_schema_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "snapshot",
                "signoz_logs",
                "a" * 64,
                "{}",
                "2026-08-01T00:00:00+00:00",
                "2026-08-01T00:00:00+00:00",
                "operator",
            ),
        )
        connection.execute(
            "INSERT INTO source_schema_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("unapproved", "signoz_logs", "d" * 64, "{}", "2026-08-01T00:00:00+00:00", None, None),
        )
        connection.execute(
            "INSERT INTO source_watermarks VALUES (?, ?, ?, ?)",
            ("other_source", 1, "source-row", "2026-08-01T00:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO project_identities "
            "(id, identity_kind, canonical_path, git_common_dir, created_at, canonical_name) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("project", "git", "/repo", "/repo/.git", "2026-08-01T00:00:00+00:00", "repo"),
        )
        connection.execute(
            "INSERT INTO scheduler_leases VALUES (?, ?, ?, ?)",
            ("scan", 1, "2026-08-01T00:00:00+00:00", "2026-08-01T00:01:00+00:00"),
        )
        connection.execute(
            "INSERT INTO scan_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "scan",
                "succeeded",
                "2026-08-01T00:00:00+00:00",
                "2026-08-01T00:01:00+00:00",
                1,
                2,
                1,
                None,
                "{}",
            ),
        )
        connection.execute(
            "INSERT INTO observations VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "observation",
                "scan",
                "detector",
                1,
                "category",
                "project",
                None,
                None,
                1,
                "b" * 64,
                "operation",
                "target",
                "target",
                "failure",
                1,
                "membership",
                "{}",
                "2026-08-01T00:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "evidence",
                "observation",
                "log",
                "log-1",
                None,
                "c" * 64,
                "correlated",
                "2026-08-01T00:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO otlp_outbox VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "event",
                "{}",
                "pending",
                0,
                "2026-08-01T00:00:00+00:00",
                "2026-08-01T00:00:00+00:00",
                None,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return path


def test_rehearsal_clears_copy_preserves_approvals_and_source(tmp_path: Path) -> None:
    source = _source(tmp_path / "source.sqlite")
    manifest = build_fresh_start_manifest(source)
    before = build_fresh_start_manifest(source)
    target = tmp_path / "fresh-reset-marker.sqlite"

    assert (
        rehearse_fresh_start_copy(source, target, manifest, "2026-08-02T00:00:00Z", "marker")
        == manifest
    )
    assert build_fresh_start_manifest(source) == before

    connection = sqlite3.connect(target)
    try:
        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM evidence").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM scan_runs").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM otlp_outbox").fetchone() == (0,)
        assert connection.execute(
            "SELECT timestamp_ns, row_id FROM source_watermarks WHERE source = 'signoz_logs'"
        ).fetchone() == (1_785_628_800_000_000_000, "")
        assert connection.execute(
            "SELECT id, approved_by FROM source_schema_snapshots"
        ).fetchone() == ("snapshot", "operator")
        assert connection.execute("SELECT COUNT(*) FROM project_identities").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM scheduler_leases").fetchone() == (0,)
        assert connection.execute(
            "SELECT id, approved_by FROM source_schema_snapshots ORDER BY id"
        ).fetchall() == [("snapshot", "operator")]
        assert connection.execute("SELECT COUNT(*) FROM source_watermarks").fetchone() == (1,)
        source_connection = sqlite3.connect(source)
        try:
            assert (
                connection.execute(
                    "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' ORDER BY name"
                ).fetchall()
                == source_connection.execute(
                    "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' ORDER BY name"
                ).fetchall()
            )
        finally:
            source_connection.close()
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_manifest_checksum_and_source_content_are_fail_closed(tmp_path: Path) -> None:
    source = _source(tmp_path / "source.sqlite")
    manifest = build_fresh_start_manifest(source)
    bad = FreshStartManifest(
        *tuple(
            getattr(manifest, name)
            for name in FreshStartManifest.__dataclass_fields__
            if name != "checksum"
        ),
        "0" * 64,
    )
    with pytest.raises(FreshStartError, match="checksum"):
        rehearse_fresh_start_copy(
            source, tmp_path / "copy-marker.sqlite", bad, "2026-08-02T00:00:00Z", "marker"
        )
    connection = sqlite3.connect(source)
    try:
        connection.execute(
            "INSERT INTO source_watermarks VALUES "
            "('signoz_logs', 1, '', '2026-08-01T00:00:00+00:00')"
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(FreshStartError, match="exactly match"):
        rehearse_fresh_start_copy(
            source, tmp_path / "changed-marker.sqlite", manifest, "2026-08-02T00:00:00Z", "marker"
        )


@pytest.mark.parametrize(
    "name, marker",
    [("copy.sqlite", "marker"), ("fresh-marker.sqlite", ""), ("source.sqlite", "marker")],
)
def test_rehearsal_refuses_unmarked_default_or_existing_target(
    tmp_path: Path, name: str, marker: str
) -> None:
    source = _source(tmp_path / "source.sqlite")
    manifest = build_fresh_start_manifest(source)
    target = tmp_path / name
    if name == "source.sqlite":
        target = source
    with pytest.raises(FreshStartError, match="target"):
        rehearse_fresh_start_copy(source, target, manifest, "2026-08-02T00:00:00Z", marker)


def test_rehearsal_refuses_existing_marked_and_default_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path / "source.sqlite")
    manifest = build_fresh_start_manifest(source)
    existing = tmp_path / "existing-marker.sqlite"
    existing.touch()
    with pytest.raises(FreshStartError, match="target"):
        rehearse_fresh_start_copy(source, existing, manifest, "2026-08-02T00:00:00Z", "marker")
    default = tmp_path / "default-marker.sqlite"
    monkeypatch.setattr(fresh_start, "DEFAULT_DATABASE_PATH", default)
    with pytest.raises(FreshStartError, match="target"):
        rehearse_fresh_start_copy(source, default, manifest, "2026-08-02T00:00:00Z", "marker")


def test_rehearsal_rolls_back_copy_when_a_delete_is_blocked(tmp_path: Path) -> None:
    source = _source(tmp_path / "source.sqlite")
    manifest = build_fresh_start_manifest(source)
    target = tmp_path / "rollback-marker.sqlite"
    # This noncanonical guard intentionally aborts the copy-only transaction.
    connection = sqlite3.connect(source)
    try:
        connection.execute(
            "CREATE TRIGGER retained_guard BEFORE DELETE ON scan_runs "
            "BEGIN SELECT RAISE(ABORT, 'stop'); END"
        )
        connection.commit()
    finally:
        connection.close()
    manifest = build_fresh_start_manifest(source)
    with pytest.raises(sqlite3.DatabaseError, match="stop"):
        rehearse_fresh_start_copy(source, target, manifest, "2026-08-02T00:00:00Z", "marker")
    connection = sqlite3.connect(target)
    try:
        assert connection.execute("SELECT id FROM scan_runs").fetchone() == ("scan",)
        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone() == (1,)
    finally:
        connection.close()


def test_cutoff_requires_offset_and_retains_exact_boundary(tmp_path: Path) -> None:
    source = _source(tmp_path / "source.sqlite")
    manifest = build_fresh_start_manifest(source)
    with pytest.raises(FreshStartError, match="UTC offset"):
        rehearse_fresh_start_copy(
            source, tmp_path / "bad-marker.sqlite", manifest, "2026-08-02", "marker"
        )
    target = tmp_path / "boundary-marker.sqlite"
    rehearse_fresh_start_copy(source, target, manifest, "2026-08-02T00:00:00+00:00", "marker")
    connection = sqlite3.connect(target)
    try:
        assert connection.execute(
            "SELECT timestamp_ns FROM source_watermarks WHERE source = 'signoz_logs'"
        ).fetchone() == (1_785_628_800_000_000_000,)
    finally:
        connection.close()

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

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
    try:
        apply_migrations(connection, path)
        connection.execute(
            "INSERT INTO source_schema_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "snapshot",
                "signoz",
                "a" * 64,
                "{}",
                "2026-08-01T00:00:00Z",
                "2026-08-01T00:00:00Z",
                "operator",
            ),
        )
        connection.execute(
            "INSERT INTO scan_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "scan",
                "succeeded",
                "2026-08-01T00:00:00Z",
                "2026-08-01T00:01:00Z",
                None,
                None,
                0,
                None,
                "{}",
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return path


def test_rehearsal_creates_empty_canonical_baseline_with_approved_evidence(tmp_path: Path) -> None:
    source = _source(tmp_path / "populated-pre-cutover.sqlite")
    manifest = build_fresh_start_manifest(source)
    target = tmp_path / "fresh-reset-marker.sqlite"

    assert (
        rehearse_fresh_start_copy(source, target, manifest, "2026-08-02T00:00:00Z", "marker")
        == manifest
    )

    connection = sqlite3.connect(target)
    try:
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        runtime_tables = tables - {"migrations", "source_schema_snapshots", "source_watermarks"}
        assert all(
            connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone() == (0,)
            for table in runtime_tables
        )
        assert connection.execute(
            "SELECT source, timestamp_ns, row_id FROM source_watermarks"
        ).fetchall() == [("signoz_logs", 1_785_628_800_000_000_000, "")]
        assert connection.execute("SELECT id FROM source_schema_snapshots").fetchall() == [
            ("snapshot",)
        ]
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_rehearsal_is_fail_closed_for_changed_source_or_manifest(tmp_path: Path) -> None:
    source = _source(tmp_path / "source.sqlite")
    manifest = build_fresh_start_manifest(source)
    bad = FreshStartManifest(
        manifest.source_migration_evidence,
        manifest.approved_source_snapshots,
        manifest.canonical_schema_identity,
        "0" * 64,
    )
    with pytest.raises(FreshStartError, match="checksum"):
        rehearse_fresh_start_copy(
            source, tmp_path / "bad-marker.sqlite", bad, "2026-08-02T00:00:00Z", "marker"
        )

    connection = sqlite3.connect(source)
    try:
        connection.execute(
            "INSERT INTO source_schema_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "changed",
                "signoz",
                "b" * 64,
                "{}",
                "2026-08-01T00:00:00Z",
                "2026-08-01T00:00:00Z",
                "operator",
            ),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(FreshStartError, match="does not exactly match"):
        rehearse_fresh_start_copy(
            source, tmp_path / "changed-marker.sqlite", manifest, "2026-08-02T00:00:00Z", "marker"
        )


@pytest.mark.parametrize(("name", "marker"), [("target.sqlite", "marker"), ("marked.sqlite", "")])
def test_rehearsal_requires_marked_new_nonlive_target(
    tmp_path: Path, name: str, marker: str
) -> None:
    source = _source(tmp_path / "source.sqlite")
    manifest = build_fresh_start_manifest(source)
    with pytest.raises(FreshStartError, match="marked"):
        rehearse_fresh_start_copy(source, tmp_path / name, manifest, "2026-08-02T00:00:00Z", marker)

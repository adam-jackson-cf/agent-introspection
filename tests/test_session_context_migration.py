import sqlite3
from pathlib import Path

import pytest

from agent_introspection.migrations import MIGRATIONS, apply_migrations


def test_session_context_producer_rebuild_preserves_rows_and_expands_enum(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        apply_migrations(connection, path, MIGRATIONS[:5])
        event_id = "a" * 64
        project_id = "b" * 64
        connection.execute(
            """
            INSERT INTO session_context_events VALUES (
                ?, 'claude-code', 'session-1', 'session_start',
                '2026-01-01T00:00:00+00:00', ?, 'project', '/project', 'git'
            )
            """,
            (event_id, project_id),
        )
        connection.execute(
            """
            INSERT INTO session_context_intervals VALUES (?, 'claude-code', 'session-1',
            '2026-01-01T00:00:00+00:00', NULL, NULL, ?, 'project', '/project', 'git')
            """,
            (event_id, project_id),
        )
        connection.commit()

        apply_migrations(connection, path)

        assert connection.execute("SELECT producer FROM session_context_events").fetchone() == (
            "claude-code",
        )
        connection.execute(
            """
            INSERT INTO session_context_events VALUES (?, 'omp', 'session-2', 'session_start',
            '2026-01-02T00:00:00+00:00', ?, 'project', '/project', 'git')
            """,
            ("c" * 64, project_id),
        )
        connection.execute(
            """
            INSERT INTO session_context_events VALUES (
                ?, 'omp', 'session-2', 'workspace_changed',
                '2026-01-02T00:01:00+00:00', ?, 'project', '/project', 'git'
            )
            """,
            ("d" * 64, project_id),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO session_context_events VALUES (
                    ?, 'omp', 'session-2', 'workspace_change',
                    '2026-01-02T00:02:00+00:00', ?, 'project', '/project', 'git'
                )
                """,
                ("e" * 64, project_id),
            )
        connection.execute(
            """
            INSERT INTO session_context_events VALUES (
                ?, 'codex-cli', 'thread-1', 'session_context',
                '2026-01-02T00:03:00+00:00', ?, 'project', '/project', 'git'
            )
            """,
            ("f" * 64, project_id),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO session_context_events VALUES (
                    ?, 'omp', 'session-2', 'session_context',
                    '2026-01-02T00:04:00+00:00', ?, 'project', '/project', 'git'
                )
                """,
                ("0" * 64, project_id),
            )
    finally:
        connection.close()

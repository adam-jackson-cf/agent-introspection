"""Local SQLite workflow store for findings and intervention proposals.

Telemetry facts live in ClickHouse; this store keeps only the workflow state
that people and model reviews write: findings, proposals and their events, and
the review sessions that draft proposals.
"""

from __future__ import annotations

import sqlite3
from importlib.resources import files
from pathlib import Path


def schema() -> str:
    """Return the idempotent workflow schema."""
    return files("agent_introspection").joinpath("workflow.sql").read_text()


def connect_workflow(path: Path | str, *, busy_timeout_ms: int = 5000) -> sqlite3.Connection:
    """Open the workflow store, creating its tables and guards when missing."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    # A rollback journal keeps the file readable by read-only openers such as the
    # dashboard, which cannot create a WAL shared-memory file.
    connection.execute("PRAGMA journal_mode = DELETE")
    connection.executescript(schema())
    columns = {row[1] for row in connection.execute("PRAGMA table_info(findings)")}
    if "subject" not in columns:
        # Stores created before findings carried a subject gain the column in place.
        connection.execute(
            "ALTER TABLE findings ADD COLUMN subject TEXT NOT NULL DEFAULT '' "
            "CHECK (subject = '' OR json_valid(subject))"
        )
    return connection

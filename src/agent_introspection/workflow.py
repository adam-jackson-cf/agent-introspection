from __future__ import annotations

import sqlite3
from importlib.resources import files
from pathlib import Path


def schema() -> str:
    return files("agent_introspection").joinpath("workflow.sql").read_text()


def connect_workflow(path: Path | str, *, busy_timeout_ms: int = 5000) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    connection.execute("PRAGMA journal_mode = DELETE")
    connection.executescript(schema())
    columns = {row[1] for row in connection.execute("PRAGMA table_info(findings)")}
    if "subject" not in columns:
        connection.execute(
            "ALTER TABLE findings ADD COLUMN subject TEXT NOT NULL DEFAULT '' "
            "CHECK (subject = '' OR json_valid(subject))"
        )
    return connection

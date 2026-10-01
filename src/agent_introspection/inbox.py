"""The activity-hook inbox and the row inserts it shares with other loaders.

Activity hooks (``hooks.py``) write one normalized
``agent-introspection.hook-event/1`` record per file into ``INBOX``. ``sync`` copies
them into ``introspection.hook_events`` and removes each file once ClickHouse has
it; files that fail to parse stay and are counted as invalid.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_introspection.facts import DATABASE, SqlRunner, sql_string

INBOX = Path.home() / ".local/share/agent-introspection/hook-inbox"
HOOK_EVENT_SCHEMA = "agent-introspection.hook-event/1"
# Rows per INSERT stay under ClickHouse's 256 KiB query limit; larger rows (hook
# events with many targets) are also cut by encoded size.
_CHUNK = 300
_CHUNK_BYTES = 192 * 1024

type Row = dict[str, Any]


def hook_event_row(document: dict[str, Any]) -> Row:
    """Flatten one activity-hook record into a ``hook_events`` row.

    Numbers and 0/1 flags go to ``attrs_number``; text goes to ``attrs_string``,
    with lists (targets) stored as JSON text.
    """
    attrs = document["attrs"]
    if not isinstance(attrs, dict):
        raise TypeError("attrs must be an object")
    strings: dict[str, str] = {}
    numbers: dict[str, float] = {}
    for key, value in attrs.items():
        if isinstance(value, bool | int | float):
            numbers[str(key)] = float(value)
        elif isinstance(value, str):
            strings[str(key)] = value
        else:
            strings[str(key)] = json.dumps(value, ensure_ascii=False)
    return {
        "event_id": str(document["event_id"]),
        "producer": str(document["producer"]),
        "session_id": str(document["session_id"]),
        "event_type": str(document["event_type"]),
        "occurred_at": str(document["occurred_at"]),
        "attrs_string": strings,
        "attrs_number": numbers,
    }


def _chunks(lines: list[str]) -> list[list[str]]:
    chunks: list[list[str]] = []
    size = 0
    for line in lines:
        if not chunks or len(chunks[-1]) >= _CHUNK or size + len(line) > _CHUNK_BYTES:
            chunks.append([])
            size = 0
        chunks[-1].append(line)
        size += len(line) + 1
    return chunks


# Column types for tables whose rows JSON inference would misread: nested objects
# would become tuples, not the Map columns the table declares.
_STRUCTURES = {
    "hook_events": (
        "event_id String, producer String, session_id String, event_type String, "
        "occurred_at DateTime64(6, 'UTC'), attrs_string Map(String, String), "
        "attrs_number Map(String, Float64)"
    ),
    "session_projects": (
        "session_id String, store String, status String, project_id String, "
        "project_name String, project_root String, resolved_by String, "
        "started_at DateTime64(3, 'UTC')"
    ),
    "prompt_labels": (
        "harness String, session_id String, prompt_key String, prompt_id String, "
        "ts DateTime64(9, 'UTC'), prompt_length UInt32, attempts UInt8, "
        "classified_at DateTime64(3, 'UTC'), status String, reason String, "
        "task_type String, task_type_confidence Float32, correction Float32, "
        "correction_kind String, sentiment String, sentiment_confidence Float32, "
        "classifier_model String, classifier_version UInt16, cost_usd Float64"
    ),
}


def insert_statements(table: str, rows: list[Row]) -> list[str]:
    """Return chunked INSERTs that parse rows as JSONEachRow inside ClickHouse."""
    structure = f"{sql_string(_STRUCTURES[table])}, " if table in _STRUCTURES else ""
    columns = ", ".join(rows[0])
    # Columns are selected by name, so the declared structure's order never matters.
    return [
        f"INSERT INTO {DATABASE}.{table} ({columns}) "
        f"SELECT {columns} FROM format(JSONEachRow, {structure}"
        + sql_string("\n".join(chunk))
        # Hook timestamps are RFC 3339 with an offset.
        + ") SETTINGS date_time_input_format = 'best_effort'"
        for chunk in _chunks([json.dumps(row) for row in rows])
    ]


def sync(run: SqlRunner, *, inbox: Path = INBOX) -> dict[str, int]:
    """Load the inbox's hook events into ClickHouse, then remove the files it holds."""
    loaded: list[tuple[Path, Row]] = []
    invalid = 0
    for path in sorted(inbox.glob("*.json")):
        try:
            document = json.loads(path.read_text())
            if document.get("schema") != HOOK_EVENT_SCHEMA:
                raise ValueError("not a hook event")
            loaded.append((path, hook_event_row(document)))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            invalid += 1
    if loaded:
        for statement in insert_statements("hook_events", [row for _, row in loaded]):
            run(statement)
    for path, _ in loaded:
        path.unlink(missing_ok=True)
    return {"hook_events": len(loaded), "invalid": invalid}


def backlog(inbox: Path = INBOX) -> int:
    """Return how many hook-event files wait in the inbox."""
    return len(list(inbox.glob("*.json"))) if inbox.exists() else 0

"""Chunked ClickHouse row inserts shared by the loaders that write rows themselves.

Hook events no longer pass through here: they travel the
OTLP route (``spool.py``) and the ``facts`` loaders read them back from SigNoz.
"""

from __future__ import annotations

import json
from typing import Any

from agent_introspection.facts import DATABASE, sql_string

# Rows per INSERT stay under ClickHouse's 256 KiB query limit; larger rows are also cut
# by encoded size.
_CHUNK = 300
_CHUNK_BYTES = 192 * 1024

type Row = dict[str, Any]


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
        # Timestamps are RFC 3339 with an offset.
        + ") SETTINGS date_time_input_format = 'best_effort'"
        for chunk in _chunks([json.dumps(row) for row in rows])
    ]

"""Workflow findings promoted from recurring tool-failure signatures in the facts.

Each run reads the last seven Europe/London calendar days of attributed tool
failures and upserts one finding per (harness, tool, failure signature):

- ``actionable``: at least 3 occurrences in 2 tasks on 2 days, or 5 occurrences
  in 3 tasks (the V8 actionable-repeat rule);
- ``emerging``: recurring across at least 2 tasks but below that threshold;
- ``dormant``: an active finding from this detector with no occurrence in the window.

Counts describe the seven-day window; ``first_seen_ns`` keeps the earliest ever seen.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from agent_introspection.facts import DATABASE, SqlRunner

DETECTOR_ID = "facts.recurrence"
DETECTOR_VERSION = 1
CATEGORY = "tool_failure_signature"

WINDOW_QUERY = f"""
SELECT harness, tool, failure_signature AS signature, count() AS occurrences,
    uniqExact(task_id) AS tasks, uniqExact(toDate(ts, 'Europe/London')) AS days,
    toUnixTimestamp64Nano(min(ts)) AS first_seen_ns, toUnixTimestamp64Nano(max(ts)) AS last_seen_ns
FROM {DATABASE}.tool_calls_snapshot
WHERE toDate(ts, 'Europe/London') > toDate(now(), 'Europe/London') - 7
    AND outcome = 'failed' AND failure_signature != '' AND task_id != ''
GROUP BY harness, tool, signature
HAVING tasks >= 2
FORMAT JSONEachRow
"""


def fingerprint(harness: str, tool: str, signature: str) -> str:
    """Stable identity of one recurring failure."""
    return hashlib.sha256(f"{DETECTOR_ID}\0{harness}\0{tool}\0{signature}".encode()).hexdigest()


def trend_state(occurrences: int, tasks: int, days: int) -> str:
    """Classify a signature that recurs across tasks in the window."""
    actionable = (occurrences >= 3 and tasks >= 2 and days >= 2) or (
        occurrences >= 5 and tasks >= 3
    )
    return "actionable" if actionable else "emerging"


def _upsert(connection: sqlite3.Connection, row: dict[str, Any], now: str) -> str:
    key = fingerprint(row["harness"], row["tool"], row["signature"])
    state = trend_state(int(row["occurrences"]), int(row["tasks"]), int(row["days"]))
    subject = json.dumps(
        {"harness": row["harness"], "tool": row["tool"], "signature": row["signature"]},
        sort_keys=True,
    )
    values = (
        state,
        int(row["last_seen_ns"]),
        int(row["occurrences"]),
        int(row["tasks"]),
        int(row["days"]),
    )
    existing = connection.execute(
        "SELECT trend_state, last_seen_ns, occurrence_count, canonical_task_count, "
        "local_day_count FROM findings WHERE fingerprint = ?",
        (key,),
    ).fetchone()
    if existing is None:
        connection.execute(
            """
            INSERT INTO findings (
                id, fingerprint, category, trend_state, detector_id, detector_version,
                first_seen_ns, last_seen_ns, occurrence_count, canonical_task_count,
                local_day_count, entity_version, updated_at, subject
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                key,
                CATEGORY,
                state,
                DETECTOR_ID,
                DETECTOR_VERSION,
                int(row["first_seen_ns"]),
                *values[1:],
                now,
                subject,
            ),
        )
        return "created"
    if tuple(existing) == values:
        return "unchanged"
    connection.execute(
        """
        UPDATE findings SET trend_state = ?, last_seen_ns = ?, occurrence_count = ?,
            canonical_task_count = ?, local_day_count = ?,
            first_seen_ns = MIN(first_seen_ns, ?), entity_version = entity_version + 1,
            updated_at = ?
        WHERE fingerprint = ?
        """,
        (*values, int(row["first_seen_ns"]), now, key),
    )
    return "updated"


def refresh(run: SqlRunner, connection: sqlite3.Connection) -> dict[str, int]:
    """Upsert findings from the current seven-day window and mark absent ones dormant."""
    rows = [json.loads(line) for line in run(WINDOW_QUERY).splitlines() if line]
    now = datetime.now(UTC).isoformat()
    outcomes = {"created": 0, "updated": 0, "unchanged": 0, "dormant": 0}
    seen = {fingerprint(row["harness"], row["tool"], row["signature"]) for row in rows}
    with connection:
        for row in rows:
            outcomes[_upsert(connection, row, now)] += 1
        stale = [
            key
            for (key,) in connection.execute(
                "SELECT fingerprint FROM findings WHERE detector_id = ? AND is_active = 1 "
                "AND trend_state != 'dormant'",
                (DETECTOR_ID,),
            )
            if key not in seen
        ]
        for key in stale:
            connection.execute(
                "UPDATE findings SET trend_state = 'dormant', occurrence_count = 0, "
                "canonical_task_count = 0, local_day_count = 0, "
                "entity_version = entity_version + 1, updated_at = ? WHERE fingerprint = ?",
                (now, key),
            )
        outcomes["dormant"] = len(stale)
    actionable = connection.execute(
        "SELECT count(*) FROM findings WHERE detector_id = ? AND trend_state = 'actionable'",
        (DETECTOR_ID,),
    ).fetchone()[0]
    return {**outcomes, "actionable": int(actionable)}

"""Workflow findings promoted from recurring problems in the facts.

Each run reads the last seven Europe/London calendar days and upserts one finding
per recurring problem from two detectors:

- ``facts.failure_cluster``: a (tool family, failure class) pair of attributed tool
  failures, which groups the same behaviour across harnesses, tool names, and the
  files it happened on. Impact is the affected tasks, with tasks that did not end
  cleanly counted twice.
- ``facts.repeated_correction``: a (project, correction kind) pair of tasks whose
  next prompt Jev read as a correction (``task_labels.corrected_next``), such as the
  user repeating an instruction the agent ignored. This is the practice nobody has
  codified yet. Impact is the corrected tasks.

Tasks in evaluation workspaces (a project or failure path under a temporary
directory) are excluded, because they are test workloads rather than work to improve.

- ``actionable``: at least 3 occurrences in 2 tasks on 2 days, or 5 occurrences
  in 3 tasks (the V8 actionable-repeat rule);
- ``emerging``: recurring across at least 2 tasks but below that threshold;
- ``dormant``: an active finding from a detector with no occurrence in the window.

Candidates are exported in impact order. Counts describe the seven-day window;
``first_seen_ns`` keeps the earliest ever seen.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from agent_introspection.facts import DATABASE, SqlRunner, enabled_filter
from agent_introspection.json_types import JsonObject

DETECTOR_ID = "facts.failure_cluster"
CORRECTION_DETECTOR_ID = "facts.repeated_correction"
DETECTOR_VERSION = 1
CATEGORY = "tool_failure_cluster"
CORRECTION_CATEGORY = "repeated_correction"
# Detectors that no longer run: their still-active findings go dormant on the next
# refresh and stay as history. The first five are the retired scan pipeline's.
RETIRED_DETECTORS = (
    "tool_failure",
    "transport_instability",
    "repeated_attempt",
    "tool_loop",
    "facts.recurrence",
)

# A project root or failure path under a temporary directory marks an evaluation
# workspace (for example `/private/tmp/luna-eval-ws/N`).
EVALUATION_PATH = r"(^|\\W)(/private)?/(tmp|var/folders)/"
_LONDON_WEEK = "toDate({ts}, 'Europe/London') > toDate(now(), 'Europe/London') - 7"
# A failure class that names only an error type or a header (`ShellError`,
# `Traceback (most recent call last):`) cannot be root-caused; it stays emerging so
# no proposal is drafted from it until sharper signatures replace it.
GENERIC_CLASS = re.compile(
    r"^(\w+(Error|Exception)|Traceback \(most recent call last\):|Script (failed|error:?))$"
)

WINDOW_QUERY = f"""
SELECT c.tool_family AS tool_family, c.failure_class AS failure_class,
    count() AS occurrences, uniqExact(c.harness, c.task_id) AS tasks,
    uniqExact(toDate(c.ts, 'Europe/London')) AS days,
    uniqExactIf((c.harness, c.task_id), t.clean_completion = 0) AS unclean_tasks,
    arraySort(groupUniqArray(c.harness)) AS harnesses,
    arraySort(groupUniqArray(c.tool)) AS tools,
    arraySort(topK(3)(c.failure_signature)) AS examples,
    toUnixTimestamp64Nano(min(c.ts)) AS first_seen_ns,
    toUnixTimestamp64Nano(max(c.ts)) AS last_seen_ns
FROM {DATABASE}.tool_calls_snapshot AS c
LEFT JOIN {DATABASE}.task_outcomes_snapshot AS t
    ON t.harness = c.harness AND t.task_id = c.task_id
LEFT JOIN {DATABASE}.session_project AS p ON p.session_id = c.session_id
WHERE {_LONDON_WEEK.format(ts="c.ts")} AND {enabled_filter("c.harness")}
    AND c.outcome = 'failed' AND c.failure_class != '' AND c.task_id != ''
    AND NOT match(p.project_root, '{EVALUATION_PATH}')
    AND NOT match(c.failure_signature, '{EVALUATION_PATH}')
GROUP BY tool_family, failure_class
HAVING tasks >= 2
FORMAT JSONEachRow
"""

CORRECTION_QUERY = f"""
SELECT p.project AS project, l.correction_kind_next AS correction_kind,
    count() AS occurrences, count() AS tasks,
    uniqExact(toDate(l.start_ts, 'Europe/London')) AS days,
    uniqExact(l.harness, l.session_id) AS sessions,
    arraySort(groupUniqArray(l.harness)) AS harnesses,
    arraySort(groupUniqArrayIf(l.task_type, l.task_type != '')) AS task_types,
    toUnixTimestamp64Nano(toDateTime64(min(l.start_ts), 9)) AS first_seen_ns,
    toUnixTimestamp64Nano(toDateTime64(max(l.start_ts), 9)) AS last_seen_ns
FROM {DATABASE}.task_labels_snapshot AS l
INNER JOIN {DATABASE}.session_project AS p ON p.session_id = l.session_id
WHERE {_LONDON_WEEK.format(ts="l.start_ts")} AND {enabled_filter("l.harness")}
    AND l.corrected_next = 1 AND l.correction_kind_next NOT IN ('', 'none')
    AND p.project != '' AND NOT match(p.project_root, '{EVALUATION_PATH}')
GROUP BY project, correction_kind
HAVING tasks >= 2 AND sessions >= 2
FORMAT JSONEachRow
"""


type SubjectBuilder = Callable[[JsonObject], JsonObject]


@dataclass(frozen=True)
class Detector:
    """One recurring-problem query and how its rows become findings."""

    detector_id: str
    category: str
    query: str
    key_fields: tuple[str, str]
    subject: SubjectBuilder


def impact(tasks: int, unclean_tasks: int) -> int:
    """Affected tasks, with tasks that did not end cleanly counted twice."""
    return tasks + unclean_tasks


def _failure_subject(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool_family": row["tool_family"],
        "failure_class": row["failure_class"],
        "harnesses": row["harnesses"],
        "tools": row["tools"],
        "examples": row["examples"],
        "impact": impact(int(row["tasks"]), int(row["unclean_tasks"])),
        "generic": GENERIC_CLASS.match(str(row["failure_class"])) is not None,
    }


def _correction_subject(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "project": row["project"],
        "correction_kind": row["correction_kind"],
        "harnesses": row["harnesses"],
        "task_types": row["task_types"],
        "sessions": int(row["sessions"]),
        "impact": int(row["tasks"]),
    }


DETECTORS = (
    Detector(
        DETECTOR_ID, CATEGORY, WINDOW_QUERY, ("tool_family", "failure_class"), _failure_subject
    ),
    Detector(
        CORRECTION_DETECTOR_ID,
        CORRECTION_CATEGORY,
        CORRECTION_QUERY,
        ("project", "correction_kind"),
        _correction_subject,
    ),
)


def fingerprint(first: str, second: str, detector_id: str = DETECTOR_ID) -> str:
    """Stable identity of one recurring problem for a detector."""
    return hashlib.sha256(f"{detector_id}\0{first}\0{second}".encode()).hexdigest()


def trend_state(occurrences: int, tasks: int, days: int, *, generic: bool = False) -> str:
    """Classify a problem that recurs across tasks in the window."""
    if generic:
        return "emerging"
    actionable = (occurrences >= 3 and tasks >= 2 and days >= 2) or (
        occurrences >= 5 and tasks >= 3
    )
    return "actionable" if actionable else "emerging"


def _key(detector: Detector, row: dict[str, Any]) -> str:
    first, second = detector.key_fields
    return fingerprint(str(row[first]), str(row[second]), detector.detector_id)


def _upsert(
    connection: sqlite3.Connection, detector: Detector, row: dict[str, Any], now: str
) -> str:
    key = _key(detector, row)
    fields = detector.subject(row)
    state = trend_state(
        int(row["occurrences"]),
        int(row["tasks"]),
        int(row["days"]),
        generic=bool(fields.get("generic")),
    )
    subject = json.dumps(fields, sort_keys=True)
    values = (
        state,
        int(row["last_seen_ns"]),
        int(row["occurrences"]),
        int(row["tasks"]),
        int(row["days"]),
        subject,
    )
    existing = connection.execute(
        "SELECT trend_state, last_seen_ns, occurrence_count, canonical_task_count, "
        "local_day_count, subject FROM findings WHERE fingerprint = ?",
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
                detector.category,
                state,
                detector.detector_id,
                DETECTOR_VERSION,
                int(row["first_seen_ns"]),
                *values[1:5],
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
            canonical_task_count = ?, local_day_count = ?, subject = ?,
            first_seen_ns = MIN(first_seen_ns, ?), entity_version = entity_version + 1,
            updated_at = ?
        WHERE fingerprint = ?
        """,
        (*values, int(row["first_seen_ns"]), now, key),
    )
    return "updated"


def _mark_dormant(connection: sqlite3.Connection, keys: list[str], now: str) -> None:
    for key in keys:
        connection.execute(
            "UPDATE findings SET trend_state = 'dormant', occurrence_count = 0, "
            "canonical_task_count = 0, local_day_count = 0, "
            "entity_version = entity_version + 1, updated_at = ? WHERE fingerprint = ?",
            (now, key),
        )


def _stale(connection: sqlite3.Connection, seen: set[str]) -> list[str]:
    detectors = (*(detector.detector_id for detector in DETECTORS), *RETIRED_DETECTORS)
    return [
        key
        for (key,) in connection.execute(
            f"SELECT fingerprint FROM findings WHERE detector_id IN "
            f"({', '.join('?' for _ in detectors)}) AND is_active = 1 "
            "AND trend_state != 'dormant'",
            detectors,
        )
        if key not in seen
    ]


def refresh(run: SqlRunner, connection: sqlite3.Connection) -> dict[str, int]:
    """Upsert findings from the current seven-day window and mark absent ones dormant.

    Findings from retired detectors are marked dormant too, so only the current
    detectors' problems stay active.
    """
    now = datetime.now(UTC).isoformat()
    outcomes = {"created": 0, "updated": 0, "unchanged": 0, "dormant": 0}
    windows = [
        (detector, [json.loads(line) for line in run(detector.query).splitlines() if line])
        for detector in DETECTORS
    ]
    seen = {_key(detector, row) for detector, rows in windows for row in rows}
    with connection:
        for detector, rows in windows:
            for row in rows:
                outcomes[_upsert(connection, detector, row, now)] += 1
        stale = _stale(connection, seen)
        _mark_dormant(connection, stale, now)
        outcomes["dormant"] = len(stale)
    actionable = connection.execute(
        "SELECT count(*) FROM findings WHERE detector_id IN (?, ?) AND trend_state = 'actionable'"
        " AND is_active = 1",
        (DETECTOR_ID, CORRECTION_DETECTOR_ID),
    ).fetchone()[0]
    return {**outcomes, "actionable": int(actionable)}

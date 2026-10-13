"""Evidence packs that let a proposal review reason about a finding's cause.

A failure-cluster pack describes one failure cluster over the last 14 days from the
facts only:
when and where it happens, the command shape, what the agent did next (whether a
retry of the same kind of tool succeeded, the failure repeated, or it moved on),
how the affected tasks ended, what the users asked for in them, and a few task
references for drill-down. A repeated-correction pack describes one project's
corrections of one kind: when they happened, the task types and effort of the
corrected tasks, the tools, commands, and failures in them, and the user's sentiment.
Nothing in a pack is raw prompt, command, or output text; signatures, classes, and
labels are the normalized forms the facts already hold.
"""

from __future__ import annotations

import json
from typing import Any

from agent_introspection.facts import DATABASE, SqlRunner, enabled_filter, sql_string

WINDOW_DAYS = 14
_C = f"{DATABASE}.tool_calls_snapshot"
_L = f"{DATABASE}.task_labels_snapshot"


def _rows(run: SqlRunner, sql: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in run(sql + "\nFORMAT JSONEachRow").splitlines() if line]


def _cluster(tool_family: str, failure_class: str, alias: str = "") -> str:
    return (
        f"{alias}tool_family = {sql_string(tool_family)}"
        f" AND {alias}failure_class = {sql_string(failure_class)}"
        f" AND {alias}outcome = 'failed' AND {alias}ts > now() - INTERVAL {WINDOW_DAYS} DAY"
        f" AND {enabled_filter(f'{alias}harness')}"
    )


def queries(tool_family: str, failure_class: str) -> dict[str, str]:
    """Return the named evidence queries for one cluster."""
    cluster = _cluster(tool_family, failure_class)
    affected = (
        f"(harness, task_id) IN (SELECT harness, task_id FROM {_C} WHERE {cluster}"
        " AND task_id != '')"
    )
    return {
        "daily": f"""SELECT toString(toDate(ts, 'Europe/London')) AS day, harness,
    count() AS failures, uniqExact(task_id) AS tasks
FROM {_C} WHERE {cluster} GROUP BY day, harness ORDER BY day, harness""",
        "projects": f"""SELECT coalesce(nullIf(p.project, ''), 'unattributed') AS project,
    count() AS failures, uniqExact(c.harness, c.task_id) AS tasks
FROM {_C} AS c LEFT JOIN {DATABASE}.session_project AS p ON p.session_id = c.session_id
WHERE {_cluster(tool_family, failure_class, "c.")}
GROUP BY project ORDER BY failures DESC LIMIT 10""",
        "shape": f"""SELECT tool, command_head, command_sub, toString(exit_code) AS exit_code,
    count() AS failures
FROM {_C} WHERE {cluster} GROUP BY tool, command_head, command_sub, exit_code
ORDER BY failures DESC LIMIT 10""",
        "signatures": f"""SELECT failure_signature AS signature, count() AS failures
FROM {_C} WHERE {cluster} GROUP BY signature ORDER BY failures DESC LIMIT 5""",
        "next_step": f"""SELECT multiIf(
        next_family = '', 'task ended',
        next_family = tool_family AND next_outcome = 'succeeded', 'same kind of tool succeeded',
        next_class = failure_class, 'same failure repeated',
        next_outcome = 'failed', 'a different failure',
        'moved to another tool') AS next_step,
    count() AS failures
FROM (
    SELECT harness, tool_family, failure_class, outcome,
        leadInFrame(tool_family) OVER w AS next_family,
        leadInFrame(outcome) OVER w AS next_outcome,
        leadInFrame(failure_class) OVER w AS next_class,
        ts
    FROM {_C}
    WHERE task_id != '' AND ts > now() - INTERVAL {WINDOW_DAYS + 1} DAY AND {affected}
    WINDOW w AS (PARTITION BY harness, task_id ORDER BY ts ROWS BETWEEN CURRENT ROW AND 1 FOLLOWING)
)
WHERE {cluster} GROUP BY next_step ORDER BY failures DESC""",
        "task_outcomes": f"""SELECT harness, count() AS tasks,
    countIf(clean_completion = 1) AS clean, countIf(clean_completion IS NOT NULL) AS clean_observed,
    countIf(quick_follow_up = 1) AS quick_follow_up
FROM {DATABASE}.task_outcomes_snapshot WHERE {affected} GROUP BY harness""",
        "task_labels": f"""SELECT task_type, count() AS tasks,
    countIf(corrected_next = 1) AS corrected_next
FROM {_L} WHERE {affected} GROUP BY task_type ORDER BY tasks DESC""",
        "examples": f"""SELECT harness, session_id, task_id, toString(max(ts)) AS last_failure,
    count() AS failures
FROM {_C} WHERE {cluster} AND task_id != '' GROUP BY harness, session_id, task_id
ORDER BY last_failure DESC LIMIT 5""",
    }


def correction_queries(project: str, correction_kind: str) -> dict[str, str]:
    """Return the named evidence queries for one project's repeated correction."""
    corrected = (
        f"l.corrected_next = 1 AND l.correction_kind_next = {sql_string(correction_kind)}"
        f" AND p.project = {sql_string(project)}"
        f" AND l.start_ts > now() - INTERVAL {WINDOW_DAYS} DAY"
        f" AND {enabled_filter('l.harness')}"
    )
    source = f"{_L} AS l INNER JOIN {DATABASE}.session_project AS p ON p.session_id = l.session_id"
    tasks = f"(harness, task_id) IN (SELECT l.harness, l.task_id FROM {source} WHERE {corrected})"
    return {
        "daily": f"""SELECT toString(toDate(l.start_ts, 'Europe/London')) AS day,
    l.harness AS harness, count() AS corrected_tasks
FROM {source} WHERE {corrected} GROUP BY day, harness ORDER BY day, harness""",
        "project_rate": f"""SELECT l.harness AS harness, countIf(l.labelled = 1) AS labelled_tasks,
    countIf(l.corrected_next = 1) AS corrected_tasks,
    countIf(l.corrected_next = 1 AND l.correction_kind_next = {sql_string(correction_kind)})
        AS this_kind
FROM {source} WHERE p.project = {sql_string(project)}
    AND l.start_ts > now() - INTERVAL {WINDOW_DAYS} DAY AND {enabled_filter("l.harness")}
GROUP BY harness""",
        "task_types": f"""SELECT l.task_type AS task_type, l.effort AS effort,
    count() AS corrected_tasks, countIf(l.sentiment_next = 'frustrated') AS frustrated_next
FROM {source} WHERE {corrected} GROUP BY task_type, effort ORDER BY corrected_tasks DESC""",
        "tools": f"""SELECT tool_family, command_head, command_sub, count() AS calls,
    countIf(outcome = 'failed') AS failed
FROM {_C} WHERE task_id != '' AND ts > now() - INTERVAL {WINDOW_DAYS + 1} DAY AND {tasks}
GROUP BY tool_family, command_head, command_sub ORDER BY calls DESC LIMIT 15""",
        "failures": f"""SELECT tool_family, failure_class, count() AS failures
FROM {_C} WHERE task_id != '' AND outcome = 'failed' AND ts > now() - INTERVAL {WINDOW_DAYS + 1} DAY
    AND {tasks}
GROUP BY tool_family, failure_class ORDER BY failures DESC LIMIT 10""",
        "examples": f"""SELECT l.harness AS harness, l.session_id AS session_id,
    l.task_id AS task_id, toString(l.start_ts) AS started, l.task_type AS task_type
FROM {source} WHERE {corrected} ORDER BY l.start_ts DESC LIMIT 5""",
    }


def pack(run: SqlRunner, subject: dict[str, Any]) -> dict[str, Any]:
    """Build the evidence pack for one finding subject of either detector."""
    if "correction_kind" in subject:
        named = correction_queries(str(subject["project"]), str(subject["correction_kind"]))
    else:
        named = queries(str(subject["tool_family"]), str(subject["failure_class"]))
    return {
        "window_days": WINDOW_DAYS,
        "cluster": subject,
        **{name: _rows(run, sql) for name, sql in named.items()},
    }

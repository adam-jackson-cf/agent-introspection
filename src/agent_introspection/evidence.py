from __future__ import annotations

import json
from typing import Any

from agent_introspection.facts import DATABASE, SqlRunner, enabled_filter, sql_string
from agent_introspection.findings import outside_evaluation

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
        f" AND {outside_evaluation(alias)}"
    )


def queries(tool_family: str, failure_class: str) -> dict[str, str]:
    cluster = _cluster(tool_family, failure_class)
    affected = (
        f"(harness, task_id) IN (SELECT harness, task_id FROM {_C} WHERE {cluster}"
        " AND task_id != '')"
    )
    return {
        "daily": f"""SELECT toString(toDate(ts, 'Europe/London')) AS day, harness,
    count() AS failures, uniqExact(task_id) AS tasks
FROM {_C} WHERE {cluster} GROUP BY day, harness ORDER BY day, harness""",
        "workdirs": f"""SELECT workdir, count() AS failures, uniqExact(harness, task_id) AS tasks
FROM {_C} WHERE {cluster} AND workdir != ''
GROUP BY workdir ORDER BY failures DESC, workdir LIMIT 10""",
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
    SELECT harness, session_id, workdir, failure_signature, tool_family, failure_class, outcome,
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


def pack(run: SqlRunner, subject: dict[str, Any]) -> dict[str, Any]:
    named = queries(str(subject["tool_family"]), str(subject["failure_class"]))
    return {
        "window_days": WINDOW_DAYS,
        "cluster": subject,
        **{name: _rows(run, sql) for name, sql in named.items()},
    }

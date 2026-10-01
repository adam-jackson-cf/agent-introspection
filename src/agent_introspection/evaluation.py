"""Evaluate applied proposals against their structured success metric.

For an applied proposal whose evaluation window has elapsed, the finding's task
rate over ``evaluation_days`` after the ``applied`` event is compared with the same
rate over ``baseline_days`` before it, counting only the metric's harnesses:

- ``cluster_task_rate``: tasks that hit the finding's failure cluster, per task;
- ``correction_task_rate``: labelled tasks in the finding's project whose next
  prompt was a correction of the finding's kind, per labelled task there.

The verdict is appended as one immutable ``evaluated`` proposal event:

- ``inconclusive`` when the baseline has fewer than 3 matching tasks or the
  evaluation window has fewer than 5 tasks;
- ``validated`` when evaluation rate <= ``max_ratio`` x baseline rate;
- ``regressed`` otherwise.

Windows that start within the snapshot retention read the snapshot tables; older
windows read the live views, which cover all loaded history.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from agent_introspection.facts import DATABASE, SNAPSHOT_DAYS, SqlRunner, sql_string
from agent_introspection.proposals import (
    CLUSTER_TASK_RATE,
    ProposalState,
    SuccessMetric,
    append_proposal_event,
    immediate_transaction,
    metric_for_subject,
)

EVENT_TYPE = "evaluated"
MIN_BASELINE_MATCHED_TASKS = 3
MIN_EVALUATION_TASKS = 5

DUE_QUERY = """
SELECT p.id, p.payload_json, f.subject,
    (SELECT e.created_at FROM proposal_events e
     WHERE e.proposal_id = p.id AND e.event_type = 'applied'
     ORDER BY e.sequence DESC LIMIT 1) AS applied_at
FROM proposals p JOIN findings f ON f.id = p.finding_id
WHERE p.state = ?
    AND NOT EXISTS (
        SELECT 1 FROM proposal_events e WHERE e.proposal_id = p.id AND e.event_type = ?
    )
ORDER BY p.created_at, p.id
"""


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class WindowRate:
    tasks: int
    matched_tasks: int
    source: str

    @property
    def rate(self) -> float | None:
        return self.matched_tasks / self.tasks if self.tasks else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "tasks": self.tasks,
            "matched_tasks": self.matched_tasks,
            "rate": self.rate,
            "source": self.source,
        }


def _literal(moment: datetime) -> str:
    text = moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
    return f"toDateTime64({sql_string(text)}, 9, 'UTC')"


def _table(view: str, window: Window, now: datetime) -> str:
    snapshot = window.start >= now - timedelta(days=SNAPSHOT_DAYS)
    return f"{DATABASE}.{view}_snapshot" if snapshot else f"{DATABASE}.{view}"


def _cluster_query(subject: dict[str, Any], names: str, window: Window, now: datetime) -> str:
    start, end = _literal(window.start), _literal(window.end)
    return f"""SELECT count() AS tasks,
    countIf((harness, task_id) IN (
        SELECT harness, task_id FROM {_table("tool_calls", window, now)}
        WHERE tool_family = {sql_string(str(subject["tool_family"]))}
            AND failure_class = {sql_string(str(subject["failure_class"]))}
            AND outcome = 'failed' AND task_id != '' AND harness IN ({names})
            AND ts >= {start}
    )) AS matched_tasks
FROM (
    SELECT DISTINCT harness, task_id FROM {_table("task_outcomes", window, now)}
    WHERE harness IN ({names}) AND task_id != ''
        AND start_ts >= {start} AND start_ts < {end}
)
FORMAT JSONEachRow"""


def _correction_query(subject: dict[str, Any], names: str, window: Window, now: datetime) -> str:
    start, end = _literal(window.start), _literal(window.end)
    kind = sql_string(str(subject["correction_kind"]))
    return f"""SELECT count() AS tasks,
    countIf(l.corrected_next = 1 AND l.correction_kind_next = {kind}) AS matched_tasks
FROM {_table("task_labels", window, now)} AS l
INNER JOIN {DATABASE}.session_project AS p ON p.session_id = l.session_id
WHERE p.project = {sql_string(str(subject["project"]))} AND l.labelled = 1
    AND l.harness IN ({names}) AND l.start_ts >= {start} AND l.start_ts < {end}
FORMAT JSONEachRow"""


def rate_query(
    metric: SuccessMetric, subject: dict[str, Any], window: Window, now: datetime
) -> str:
    """Return the tasks and matching tasks started in ``window`` for the metric's harnesses."""
    names = ", ".join(sql_string(name) for name in metric.harnesses)
    build = _cluster_query if metric.metric == CLUSTER_TASK_RATE else _correction_query
    return build(subject, names, window, now)


def window_rate(
    run: SqlRunner,
    metric: SuccessMetric,
    subject: dict[str, Any],
    window: Window,
    now: datetime,
) -> WindowRate:
    """Measure one window's task rate."""
    output = run(rate_query(metric, subject, window, now))
    rows = [json.loads(line) for line in output.splitlines() if line]
    row = rows[0] if rows else {"tasks": 0, "matched_tasks": 0}
    view = "tool_calls" if metric.metric == CLUSTER_TASK_RATE else "task_labels"
    table = _table(view, window, now).removeprefix(f"{DATABASE}.")
    return WindowRate(int(row["tasks"]), int(row["matched_tasks"]), table)


def verdict(metric: SuccessMetric, baseline: WindowRate, evaluation: WindowRate) -> str:
    """Classify the evaluation window against the baseline and the metric's ratio."""
    if (
        baseline.matched_tasks < MIN_BASELINE_MATCHED_TASKS
        or evaluation.tasks < MIN_EVALUATION_TASKS
        or baseline.rate is None
        or evaluation.rate is None
    ):
        return "inconclusive"
    return "validated" if evaluation.rate <= metric.max_ratio * baseline.rate else "regressed"


def evaluate(
    run: SqlRunner,
    subject: dict[str, Any],
    metric: SuccessMetric,
    applied_at: datetime,
    now: datetime,
) -> dict[str, Any]:
    """Return the ``evaluated`` event payload for one applied proposal."""
    baseline_window = Window(applied_at - timedelta(days=metric.baseline_days), applied_at)
    evaluation_window = Window(applied_at, applied_at + timedelta(days=metric.evaluation_days))
    baseline = window_rate(run, metric, subject, baseline_window, now)
    evaluation = window_rate(run, metric, subject, evaluation_window, now)
    ratio = (
        evaluation.rate / baseline.rate if baseline.rate and evaluation.rate is not None else None
    )
    return {
        "metric": metric.metric,
        "harnesses": list(metric.harnesses),
        "max_ratio": metric.max_ratio,
        "applied_at": applied_at.isoformat(),
        "baseline_window": [baseline_window.start.isoformat(), baseline_window.end.isoformat()],
        "evaluation_window": [
            evaluation_window.start.isoformat(),
            evaluation_window.end.isoformat(),
        ],
        "baseline": baseline.as_dict(),
        "evaluation": evaluation.as_dict(),
        "baseline_rate": baseline.rate,
        "evaluation_rate": evaluation.rate,
        "ratio": ratio,
        "verdict": verdict(metric, baseline, evaluation),
        "evaluated_at": now.isoformat(),
    }


def _append_once(connection: sqlite3.Connection, proposal_id: str, payload: dict[str, Any]) -> bool:
    with immediate_transaction(connection):
        exists = connection.execute(
            "SELECT 1 FROM proposal_events WHERE proposal_id = ? AND event_type = ?",
            (proposal_id, EVENT_TYPE),
        ).fetchone()
        if exists is not None:
            return False
        append_proposal_event(connection, proposal_id, EVENT_TYPE, payload)
    return True


def _skip(proposal_id: str, reason: str) -> dict[str, str]:
    return {"proposal_id": proposal_id, "reason": reason}


def evaluate_due(
    run: SqlRunner, connection: sqlite3.Connection, now: datetime | None = None
) -> dict[str, Any]:
    """Evaluate every applied proposal whose evaluation window has elapsed, once each."""
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    evaluated: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    rows = connection.execute(DUE_QUERY, (ProposalState.APPLIED, EVENT_TYPE)).fetchall()
    for proposal_id, payload_json, subject_json, applied_text in rows:
        metric = SuccessMetric.from_payload(json.loads(payload_json))
        subject = json.loads(subject_json) if subject_json else {}
        if metric is None:
            skipped.append(_skip(proposal_id, "legacy free-text success metric"))
            continue
        if metric_for_subject(subject) != metric.metric or applied_text is None:
            skipped.append(_skip(proposal_id, "finding subject does not match the metric"))
            continue
        applied_at = datetime.fromisoformat(applied_text).astimezone(UTC)
        if moment < applied_at + timedelta(days=metric.evaluation_days):
            skipped.append(_skip(proposal_id, "evaluation window has not elapsed"))
            continue
        payload = evaluate(run, subject, metric, applied_at, moment)
        if _append_once(connection, proposal_id, payload):
            evaluated.append({
                "proposal_id": proposal_id,
                "verdict": payload["verdict"],
                "ratio": payload["ratio"],
            })
    return {"evaluated": evaluated, "skipped": skipped}

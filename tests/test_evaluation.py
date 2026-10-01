import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

import pytest

from agent_introspection import evaluation
from agent_introspection.proposals import (
    ProposalState,
    TransitionProposalRequest,
    create_proposal,
    transition_proposal,
)
from tests.test_proposals import (
    PASSED_VALIDATION,
    SUCCESS_METRIC,
    proposal_database,
    proposal_input,
)

CLUSTER = {"tool_family": "shell", "failure_class": "not_found", "harnesses": ["codex_exec"]}
CORRECTION = {"project": "example", "correction_kind": "ignored", "harnesses": ["codex_exec"]}


def applied_proposal(
    subject: dict[str, Any] = CLUSTER, metric: dict[str, Any] = SUCCESS_METRIC
) -> tuple[sqlite3.Connection, str, datetime]:
    connection = proposal_database()
    connection.execute("UPDATE findings SET subject = ?", (json.dumps(subject),))
    connection.commit()
    proposal_id = create_proposal(
        connection, replace(proposal_input(), predicted_success_metric=metric)
    )
    for state, evidence in (
        (ProposalState.APPROVED, {"decision": "approve"}),
        (ProposalState.APPLYING, {"request": "mark-applied"}),
        (ProposalState.APPLIED, PASSED_VALIDATION),
    ):
        transition_proposal(
            connection,
            TransitionProposalRequest(
                proposal_id, state, "user", evidence, explicit_application_request=True
            ),
        )
    applied_at = datetime.fromisoformat(
        connection.execute(
            "SELECT created_at FROM proposal_events WHERE event_type = 'applied'"
        ).fetchone()[0]
    )
    return connection, proposal_id, applied_at


def facts(*windows: tuple[int, int]) -> tuple[list[str], Any]:
    statements: list[str] = []
    responses = [json.dumps({"tasks": t, "matched_tasks": m}) + "\n" for t, m in windows]

    def run(sql: str) -> str:
        statements.append(sql)
        return responses[len(statements) - 1]

    return statements, run


def events(connection: sqlite3.Connection, proposal_id: str) -> list[tuple[int, str]]:
    return connection.execute(
        "SELECT sequence, event_type FROM proposal_events WHERE proposal_id = ? ORDER BY sequence",
        (proposal_id,),
    ).fetchall()


@pytest.mark.parametrize(
    ("baseline", "after", "expected"),
    [
        ((10, 5), (10, 2), "validated"),
        ((10, 5), (10, 3), "regressed"),
        ((10, 2), (10, 0), "inconclusive"),
        ((10, 5), (4, 0), "inconclusive"),
    ],
)
def test_due_proposal_gets_one_immutable_evaluated_event(
    baseline: tuple[int, int], after: tuple[int, int], expected: str
) -> None:
    connection, proposal_id, applied_at = applied_proposal()
    statements, run = facts(baseline, after)

    result = evaluation.evaluate_due(run, connection, applied_at + timedelta(days=15))

    assert [item["verdict"] for item in result["evaluated"]] == [expected]
    assert events(connection, proposal_id)[-1] == (5, "evaluated")
    payload = json.loads(
        connection.execute(
            "SELECT payload_json FROM proposal_events WHERE event_type = 'evaluated'"
        ).fetchone()[0]
    )
    assert payload["verdict"] == expected
    assert payload["baseline_rate"] == baseline[1] / baseline[0]
    assert payload["evaluation_rate"] == after[1] / after[0]
    assert payload["ratio"] == pytest.approx((after[1] / after[0]) / (baseline[1] / baseline[0]))
    assert len(statements) == 2
    assert all("tool_calls_snapshot" in sql and "'codex_exec'" in sql for sql in statements)
    assert all("tool_family = 'shell'" in sql for sql in statements)
    assert state(connection, proposal_id) == "applied"

    again = evaluation.evaluate_due(run, connection, applied_at + timedelta(days=16))
    assert again == {"evaluated": [], "skipped": []}
    assert len(statements) == 2


def state(connection: sqlite3.Connection, proposal_id: str) -> str:
    return str(
        connection.execute("SELECT state FROM proposals WHERE id = ?", (proposal_id,)).fetchone()[0]
    )


def test_proposal_is_not_evaluated_before_its_window_elapses() -> None:
    connection, proposal_id, applied_at = applied_proposal()
    statements, run = facts()

    result = evaluation.evaluate_due(run, connection, applied_at + timedelta(days=13))

    assert result["skipped"] == [
        {"proposal_id": proposal_id, "reason": "evaluation window has not elapsed"}
    ]
    assert statements == []
    assert events(connection, proposal_id)[-1][1] == "applied"


def test_windows_older_than_the_snapshot_read_the_live_views() -> None:
    connection, _, applied_at = applied_proposal()
    statements, run = facts((10, 5), (10, 1))

    evaluation.evaluate_due(run, connection, applied_at + timedelta(days=200))

    assert all("introspection.tool_calls\n" in sql for sql in statements)
    assert all("introspection.task_outcomes\n" in sql for sql in statements)
    assert all("_snapshot" not in sql for sql in statements)


def test_correction_findings_use_the_correction_task_rate() -> None:
    metric = SUCCESS_METRIC | {"metric": "correction_task_rate"}
    connection, _, applied_at = applied_proposal(CORRECTION, metric)
    statements, run = facts((20, 6), (20, 2))

    result = evaluation.evaluate_due(run, connection, applied_at + timedelta(days=15))

    assert [item["verdict"] for item in result["evaluated"]] == ["validated"]
    assert all("task_labels_snapshot" in sql for sql in statements)
    assert all("p.project = 'example'" in sql for sql in statements)
    assert all("correction_kind_next = 'ignored'" in sql for sql in statements)
    assert all(
        "l.harness IN (SELECT harness FROM introspection.harnesses" in sql for sql in statements
    )


def test_cluster_rates_count_only_the_enabled_harnesses() -> None:
    connection, _, applied_at = applied_proposal()
    statements, run = facts((20, 6), (20, 2))

    evaluation.evaluate_due(run, connection, applied_at + timedelta(days=15))

    enabled = "harness IN (SELECT harness FROM introspection.harnesses WHERE enabled = 1)"
    assert statements
    assert all(sql.count(enabled) == 2 for sql in statements)


def test_legacy_free_text_metrics_are_skipped() -> None:
    connection, proposal_id, applied_at = applied_proposal()
    connection.execute(
        "INSERT INTO proposals VALUES ('legacy', 'finding-1', 'applied', ?, 'a', 'b', 1)",
        (json.dumps({"predicted_success_metric": "fewer failures"}),),
    )
    connection.commit()
    _, run = facts((10, 5), (10, 1))

    result = evaluation.evaluate_due(run, connection, applied_at + timedelta(days=15))

    assert [item["proposal_id"] for item in result["evaluated"]] == [proposal_id]
    assert result["skipped"] == [
        {"proposal_id": "legacy", "reason": "legacy free-text success metric"}
    ]


def test_cluster_query_ignores_matching_failures_after_the_window_end() -> None:
    connection, _, applied_at = applied_proposal()
    statements, run = facts((10, 5), (10, 1))

    evaluation.evaluate_due(run, connection, applied_at + timedelta(days=15))

    baseline = statements[0]
    end = evaluation._literal(applied_at)
    assert f"AND ts >= {evaluation._literal(applied_at - timedelta(days=14))} AND ts < {end}" in (
        baseline.replace("\n", " ")
    )

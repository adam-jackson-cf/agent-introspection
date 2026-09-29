import json
import sqlite3
from pathlib import Path

from agent_introspection import findings
from agent_introspection.workflow import connect_workflow


def window(*rows: dict[str, object]) -> findings.SqlRunner:
    return lambda _sql: "\n".join(json.dumps(row) for row in rows)


def row(
    signature: str, occurrences: int, tasks: int, days: int, last: int = 20
) -> dict[str, object]:
    return {
        "harness": "codex_exec",
        "tool": "exec_command",
        "signature": signature,
        "occurrences": occurrences,
        "tasks": tasks,
        "days": days,
        "first_seen_ns": 10,
        "last_seen_ns": last,
    }


def states(connection: sqlite3.Connection) -> dict[str, tuple[str, int]]:
    return {
        json.loads(subject)["signature"]: (state, version)
        for subject, state, version in connection.execute(
            "SELECT subject, trend_state, entity_version FROM findings"
        )
    }


def test_refresh_promotes_recurring_signatures_by_the_actionable_rule() -> None:
    connection = connect_workflow(":memory:")

    result = findings.refresh(
        window(row("typo path", 3, 2, 2), row("five in three", 5, 3, 1), row("two tasks", 2, 2, 1)),
        connection,
    )

    assert result == {"created": 3, "updated": 0, "unchanged": 0, "dormant": 0, "actionable": 2}
    assert states(connection) == {
        "typo path": ("actionable", 1),
        "five in three": ("actionable", 1),
        "two tasks": ("emerging", 1),
    }


def test_refresh_versions_changes_and_marks_vanished_findings_dormant() -> None:
    connection = connect_workflow(":memory:")
    findings.refresh(window(row("steady", 3, 2, 2), row("gone", 3, 2, 2)), connection)

    result = findings.refresh(window(row("steady", 3, 2, 2), row("new", 2, 2, 1)), connection)
    assert result["unchanged"] == 1
    assert result["dormant"] == 1
    grown = findings.refresh(
        window(row("steady", 4, 3, 2, last=30), row("new", 2, 2, 1)), connection
    )

    assert grown["updated"] == 1
    assert states(connection) == {
        "steady": ("actionable", 2),
        "gone": ("dormant", 2),
        "new": ("emerging", 1),
    }


def test_existing_stores_gain_the_subject_column(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite3"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE findings (id TEXT PRIMARY KEY, fingerprint TEXT)")
    old.commit()
    old.close()

    connection = connect_workflow(path)

    assert "subject" in {column[1] for column in connection.execute("PRAGMA table_info(findings)")}

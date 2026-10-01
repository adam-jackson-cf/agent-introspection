import json
import sqlite3
from pathlib import Path

from agent_introspection import findings
from agent_introspection.workflow import connect_workflow


def window(
    *rows: dict[str, object], corrections: tuple[dict[str, object], ...] = ()
) -> findings.SqlRunner:
    def run(sql: str) -> str:
        chosen = corrections if sql == findings.CORRECTION_QUERY else rows
        return "\n".join(json.dumps(row) for row in chosen)

    return run


def row(
    signature: str, occurrences: int, tasks: int, days: int, **overrides: object
) -> dict[str, object]:
    return {
        "tool_family": "shell",
        "failure_class": signature,
        "occurrences": occurrences,
        "tasks": tasks,
        "days": days,
        "unclean_tasks": 0,
        "harnesses": ["codex_exec", "oh-my-pi"],
        "tools": ["bash", "exec_command"],
        "examples": [signature],
        "first_seen_ns": 10,
        "last_seen_ns": 20,
        **overrides,
    }


def correction(kind: str, tasks: int, days: int, sessions: int = 2) -> dict[str, object]:
    return {
        "project": "demo",
        "correction_kind": kind,
        "occurrences": tasks,
        "tasks": tasks,
        "days": days,
        "sessions": sessions,
        "harnesses": ["claude-code"],
        "task_types": ["bugfix"],
        "first_seen_ns": 10,
        "last_seen_ns": 20,
    }


def states(connection: sqlite3.Connection) -> dict[str, tuple[str, int]]:
    result = {}
    for subject, state, version in connection.execute(
        "SELECT subject, trend_state, entity_version FROM findings"
    ):
        parsed = json.loads(subject)
        result[parsed.get("failure_class") or parsed["correction_kind"]] = (state, version)
    return result


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
        window(row("steady", 4, 3, 2, last_seen_ns=30), row("new", 2, 2, 1)), connection
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


def test_dormant_findings_clear_their_window_counts() -> None:
    connection = connect_workflow(":memory:")
    findings.refresh(window(row("gone", 3, 2, 2)), connection)

    findings.refresh(window(), connection)

    assert connection.execute(
        "SELECT trend_state, occurrence_count, canonical_task_count, local_day_count FROM findings"
    ).fetchone() == ("dormant", 0, 0, 0)


def test_subject_records_the_cluster_and_its_impact() -> None:
    connection = connect_workflow(":memory:")

    findings.refresh(window(row("Path '…' not found", 4, 3, 2, unclean_tasks=1)), connection)

    (subject,) = connection.execute("SELECT subject FROM findings").fetchone()
    assert json.loads(subject) == {
        "tool_family": "shell",
        "failure_class": "Path '…' not found",
        "harnesses": ["codex_exec", "oh-my-pi"],
        "tools": ["bash", "exec_command"],
        "examples": ["Path '…' not found"],
        "impact": 4,
        "generic": False,
    }


def test_the_same_cluster_across_harnesses_is_one_finding() -> None:
    assert findings.fingerprint("shell", "x") == findings.fingerprint("shell", "x")
    assert findings.fingerprint("shell", "x") != findings.fingerprint("read", "x")
    assert findings.fingerprint("demo", "x", findings.CORRECTION_DETECTOR_ID) != (
        findings.fingerprint("demo", "x")
    )


def test_repeated_corrections_become_findings_of_their_own_detector() -> None:
    connection = connect_workflow(":memory:")

    result = findings.refresh(
        window(
            corrections=(correction("ignored_instruction", 3, 2), correction("incomplete", 2, 1))
        ),
        connection,
    )

    assert result["created"] == 2
    assert result["actionable"] == 1
    rows = dict(
        connection.execute(
            "SELECT json_extract(subject, '$.correction_kind'), detector_id FROM findings"
        )
    )
    assert rows == {
        "ignored_instruction": findings.CORRECTION_DETECTOR_ID,
        "incomplete": findings.CORRECTION_DETECTOR_ID,
    }


def test_findings_from_the_retired_detector_go_dormant() -> None:
    connection = connect_workflow(":memory:")
    connection.execute(
        "INSERT INTO findings (id, fingerprint, category, trend_state, detector_id, "
        "detector_version, first_seen_ns, last_seen_ns, occurrence_count, "
        "canonical_task_count, local_day_count, entity_version, updated_at, subject) "
        "VALUES ('old', ?, 'tool_failure_signature', 'actionable', 'facts.recurrence', "
        "1, 1, 2, 3, 2, 2, 1, '2026-09-29', '')",
        ("a" * 64,),
    )

    result = findings.refresh(window(), connection)

    assert result["dormant"] == 1
    assert connection.execute("SELECT trend_state FROM findings").fetchone() == ("dormant",)


def test_evaluation_workspaces_are_excluded() -> None:
    for query in (findings.WINDOW_QUERY, findings.CORRECTION_QUERY):
        assert f"NOT match(p.project_root, '{findings.EVALUATION_PATH}')" in query


def test_generic_failure_classes_never_become_actionable() -> None:
    connection = connect_workflow(":memory:")

    findings.refresh(
        window(
            row("ShellError", 9, 5, 3),
            row("Traceback (most recent call last):", 9, 5, 3),
            row("ModuleNotFoundError: No module named '…'", 9, 5, 3),
        ),
        connection,
    )

    assert {k: v[0] for k, v in states(connection).items()} == {
        "ShellError": "emerging",
        "Traceback (most recent call last):": "emerging",
        "ModuleNotFoundError: No module named '…'": "actionable",
    }

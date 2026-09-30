import json
import plistlib
import sqlite3
from pathlib import Path

import pytest

from agent_introspection import facts, projects
from agent_introspection.facts import FactsError

EVENT = {
    "event_id": "e" * 64,
    "producer": "omp",
    "session_id": "019a-session",
    "event_type": "session_start",
    "occurred_at": "2026-09-28T10:00:00.000000Z",
    "agent": {
        "project": {
            "id": "p" * 64,
            "name": "dashboard",
            "root": "/Users/someone/Projects/dashboard",
            "kind": "git",
        }
    },
}
REJECTION = {
    "rejection_id": "r" * 64,
    "producer": "claude-code",
    "producer_surface": "session-context-inbox",
    "correlation_id": "c1",
    "lifecycle_event": "session_start",
    "occurred_at": "2026-09-28T10:00:01.000000Z",
    "reason_code": "non_git_workspace",
    "source_adapter": "claude-code",
}


def write_inbox(inbox: Path) -> None:
    inbox.mkdir()
    (inbox / "event.json").write_text(json.dumps(EVENT))
    (inbox / "rejection.json").write_text(json.dumps(REJECTION))
    (inbox / "broken.json").write_text("{not json")
    (inbox / ".partial.json.tmp").write_text("in flight")


def parsed_rows(statements: list[str]) -> dict[str, list[dict[str, str]]]:
    rows: dict[str, list[dict[str, str]]] = {}
    for statement in statements:
        table = statement.split("INSERT INTO introspection.")[1].split(" ")[0]
        literal = statement.split("format(JSONEachRow, '", 1)[1].rsplit("') SETTINGS", 1)[0]
        text = literal.replace("\\'", "'").replace("\\\\", "\\")
        rows.setdefault(table, []).extend(json.loads(line) for line in text.splitlines())
    return rows


def test_sync_loads_events_and_rejections_then_removes_only_loaded_files(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    write_inbox(inbox)
    executed: list[str] = []

    result = projects.sync(lambda sql: executed.append(sql) or "", inbox=inbox)

    assert result == {"events": 1, "rejections": 1, "ledger_events": 0, "invalid": 1}
    rows = parsed_rows(executed)
    assert rows["session_projects"][0]["project_root"] == "~/Projects/dashboard"
    assert rows["session_projects"][0]["session_id"] == "019a-session"
    assert rows["session_project_rejections"][0]["reason_code"] == "non_git_workspace"
    assert sorted(path.name for path in inbox.iterdir()) == [".partial.json.tmp", "broken.json"]


def test_sync_keeps_every_inbox_file_when_clickhouse_rejects_the_insert(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    write_inbox(inbox)

    def failing(_sql: str) -> str:
        raise FactsError("Code: 241. DB::Exception: Memory limit exceeded")

    with pytest.raises(FactsError):
        projects.sync(failing, inbox=inbox)

    assert {"event.json", "rejection.json"} <= {path.name for path in inbox.iterdir()}


def test_sync_keeps_every_inbox_file_when_a_later_insert_fails_and_retry_is_deduplicated(
    tmp_path: Path,
) -> None:
    inbox = tmp_path / "inbox"
    write_inbox(inbox)
    attempted: list[str] = []

    def fails_on_rejections(sql: str) -> str:
        attempted.append(sql)
        if "session_project_rejections" in sql:
            raise FactsError("Code: 241. DB::Exception: Memory limit exceeded")
        return ""

    with pytest.raises(FactsError):
        projects.sync(fails_on_rejections, inbox=inbox)

    assert [s.split(" ")[2] for s in attempted] == [
        "introspection.session_projects",
        "introspection.session_project_rejections",
    ]
    assert {"event.json", "rejection.json"} <= {path.name for path in inbox.iterdir()}

    retried: list[str] = []
    projects.sync(lambda sql: retried.append(sql) or "", inbox=inbox)

    # The retry re-sends the event that already landed; the tables collapse it by key.
    assert parsed_rows(retried)["session_projects"] == parsed_rows(attempted)["session_projects"]
    tables = facts._sql("001_tables.sql")
    for table, key in (
        ("session_projects", "ORDER BY (session_id, event_id)"),
        ("session_project_rejections", "ORDER BY rejection_id"),
    ):
        ddl = tables.split(f"CREATE TABLE IF NOT EXISTS introspection.{table}\n", 1)[1]
        ddl = ddl.split(";", 1)[0]
        assert "ENGINE = ReplacingMergeTree" in ddl
        assert key in ddl
    assert {path.name for path in inbox.iterdir()} == {".partial.json.tmp", "broken.json"}


def test_sync_imports_retired_ledger_history_with_home_redacted(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.sqlite3"
    connection = sqlite3.connect(ledger)
    connection.execute(
        "CREATE TABLE session_context_events (event_id TEXT, producer TEXT, session_id TEXT, "
        "event_type TEXT, occurred_at TEXT, project_id TEXT, project_name TEXT, "
        "project_root TEXT, project_kind TEXT)"
    )
    connection.executemany(
        "INSERT INTO session_context_events VALUES (?, 'codex-cli', ?, 'session_start', "
        "'2026-08-24T10:00:00+00:00', 'p', 'repo', '/Users/someone/repo', 'git')",
        [(f"event-{i}", f"session-{i}") for i in range(2_500)],
    )
    connection.commit()
    connection.close()
    executed: list[str] = []

    result = projects.sync(
        lambda sql: executed.append(sql) or "", inbox=tmp_path / "none", ledger=ledger
    )

    assert result["ledger_events"] == 2_500
    assert all(len(statement) < 256 * 1024 for statement in executed)
    history = parsed_rows(executed)["session_projects"]
    assert len(history) == 2_500
    assert {row["project_root"] for row in history} == {"~/repo"}


def test_launchd_job_runs_the_sync_every_minute_with_docker_on_path() -> None:
    document = plistlib.loads(
        projects.plist(Path("/venv/bin/agent-introspection"), ["/opt/docker/bin", "/usr/bin"])
    )

    assert document["Label"] == projects.LABEL
    assert document["ProgramArguments"] == [
        "/venv/bin/agent-introspection",
        "facts",
        "sync",
    ]
    assert document["StartInterval"] == 60
    assert document["EnvironmentVariables"]["PATH"] == "/opt/docker/bin:/usr/bin"

import json
from pathlib import Path

import pytest

from agent_introspection import inbox
from agent_introspection.facts import FactsError, SqlRunner

EVENT = {
    "schema": inbox.HOOK_EVENT_SCHEMA,
    "event_id": "e" * 64,
    "producer": "claude-code",
    "session_id": "019a-session",
    "event_type": "tool_call",
    "occurred_at": "2026-10-01T10:00:00.000000Z",
    "attrs": {"tool_name": "Bash", "gate_bypass": 0, "targets": ["~/p/a.py"]},
}


def recorder(log: list[str]) -> SqlRunner:
    def run(sql: str) -> str:
        log.append(sql)
        return ""

    return run


def write_inbox(directory: Path) -> None:
    directory.mkdir()
    (directory / "event.json").write_text(json.dumps(EVENT))
    (directory / "broken.json").write_text("{not json")
    (directory / "old-session-context.json").write_text(json.dumps({"event_id": "x"}))
    (directory / ".partial.json.tmp").write_text("{}")


def test_sync_loads_hook_events_and_removes_only_loaded_files(tmp_path: Path) -> None:
    directory = tmp_path / "inbox"
    write_inbox(directory)
    executed: list[str] = []

    result = inbox.sync(recorder(executed), inbox=directory)

    assert result == {"hook_events": 1, "invalid": 2}
    (statement,) = executed
    assert statement.startswith("INSERT INTO introspection.hook_events")
    assert sorted(path.name for path in directory.iterdir()) == [
        ".partial.json.tmp",
        "broken.json",
        "old-session-context.json",
    ]
    assert inbox.backlog(directory) == 2


def test_sync_keeps_every_file_when_clickhouse_rejects_the_insert(tmp_path: Path) -> None:
    directory = tmp_path / "inbox"
    write_inbox(directory)

    def failing(_sql: str) -> str:
        raise FactsError("Code: 241. DB::Exception: Memory limit exceeded")

    with pytest.raises(FactsError):
        inbox.sync(failing, inbox=directory)

    assert "event.json" in {path.name for path in directory.iterdir()}


def test_an_absent_inbox_has_no_backlog(tmp_path: Path) -> None:
    assert inbox.backlog(tmp_path / "none") == 0
    assert inbox.sync(recorder([]), inbox=tmp_path / "none") == {"hook_events": 0, "invalid": 0}

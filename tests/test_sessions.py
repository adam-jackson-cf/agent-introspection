import json
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from agent_introspection import sessions
from agent_introspection.facts import SqlRunner


def store(name: str, root: Path, **overrides: Any) -> sessions.Store:
    fields: dict[str, Any] = {
        "name": name,
        "harnesses": (name,),
        "roots": (str(root),),
        "glob": "*/*.jsonl",
        "max_lines": 5,
        "match": {},
        "session_id": "sessionId",
        "cwd": "cwd",
        "started_at": "timestamp",
    }
    fields.update(overrides)
    return sessions.Store(**fields)


def write_jsonl(path: Path, *records: Mapping[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    return path


def git(*args: str) -> None:
    """Run git in a test repository, never in one an inherited GIT_DIR names."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    subprocess.run(["git", *args], check=True, timeout=30, env=environment)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "work" / "demo"
    (root / "src").mkdir(parents=True)
    git("init", "-q", str(root))
    return root


def recorder(log: list[str]) -> SqlRunner:
    def run(sql: str) -> str:
        log.append(sql)
        return ""

    return run


def rows_of(statements: list[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for statement in statements:
        # The declared structure ends with DateTime64(3, \'UTC\'); the rows follow it.
        literal = statement.split("UTC\\')', '", 1)[1].rsplit("') SETTINGS", 1)[0]
        text = literal.replace("\\'", "'").replace("\\\\", "\\")
        rows.extend(json.loads(line) for line in text.splitlines())
    return rows


def test_the_bundled_stores_name_every_known_harness() -> None:
    stores = sessions.load_stores({"codex": ["~/orca/*/home/sessions"]})
    harnesses = {harness for entry in stores for harness in entry.harnesses}
    assert harnesses == {
        "claude-code",
        "codex-app-server",
        "codex_cli_rs",
        "codex_exec",
        "oh-my-pi",
    }
    codex = next(entry for entry in stores if entry.name == "codex")
    assert codex.roots[-1] == "~/orca/*/home/sessions"


def test_a_record_found_by_match_and_dotted_paths(tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path / "s" / "rollout-1.jsonl",
        {
            "type": "session_meta",
            "timestamp": "2026-10-01T10:00:00Z",
            "payload": {"id": "c1", "cwd": "/x"},
        },
    )
    codex = store(
        "codex",
        tmp_path,
        match={"type": "session_meta"},
        session_id="payload.id",
        cwd="payload.cwd",
        max_lines=1,
    )
    assert sessions.read_session(path, codex) == sessions.Session(
        "c1", "/x", "2026-10-01T10:00:00Z"
    )


def test_a_record_past_the_line_limit_is_not_there_yet(tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path / "p" / "s.jsonl", *[{"type": "summary"}] * 5, {"sessionId": "a", "cwd": "/x"}
    )
    assert sessions.read_session(path, store("claude-code", tmp_path)) is None


def test_projects_resolve_to_the_main_repository_and_name_their_reason(
    repo: Path, tmp_path: Path
) -> None:
    attributed = sessions.resolve_project(str(repo / "src"))
    assert attributed.status == "attributed"
    assert attributed.name == "demo"
    assert len(attributed.project_id) == 64
    plain = tmp_path / "plain"
    plain.mkdir()
    assert sessions.resolve_project(str(plain)).status == "non_git_workspace"
    assert sessions.resolve_project(str(tmp_path / "gone")).status == "missing_workspace"
    assert sessions.resolve_project("relative/dir").status == "missing_workspace"


def test_resolve_stores_each_session_once_and_retries_files_without_a_record(
    repo: Path, tmp_path: Path
) -> None:
    root = tmp_path / "projects"
    write_jsonl(
        root / "a" / "one.jsonl",
        {"sessionId": "s1", "cwd": str(repo), "timestamp": "2026-10-01T10:00:00Z"},
    )
    pending = write_jsonl(root / "a" / "two.jsonl", {"type": "summary"})
    stores = [store("claude-code", root)]
    state = tmp_path / "scan.json"
    executed: list[str] = []

    first = sessions.resolve(recorder(executed), stores=stores, state=state)

    assert first == {"sessions": 1, "attributed": 1}
    (row,) = rows_of(executed)
    assert (row["session_id"], row["store"], row["project_name"]) == ("s1", "claude-code", "demo")
    assert "prompt" not in json.dumps(row)

    write_jsonl(
        pending, {"sessionId": "s2", "cwd": "/nowhere", "timestamp": "2026-10-01T11:00:00Z"}
    )
    again: list[str] = []
    second = sessions.resolve(recorder(again), stores=stores, state=state)

    assert second == {"sessions": 1, "missing_workspace": 1}
    assert [row["session_id"] for row in rows_of(again)] == ["s2"]
    rescan: list[str] = []
    assert (
        sessions.resolve(recorder(rescan), stores=stores, state=state, rescan=True)["sessions"] == 2
    )


def test_an_unset_root_variable_is_skipped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("INTROSPECTION_TEST_UNSET", raising=False)
    assert sessions.expand_root("$INTROSPECTION_TEST_UNSET/sessions") == []
    (tmp_path / "a" / "sessions").mkdir(parents=True)
    monkeypatch.setenv("INTROSPECTION_TEST_ROOT", str(tmp_path))
    assert sessions.expand_root("$INTROSPECTION_TEST_ROOT/*/sessions") == [
        tmp_path / "a" / "sessions"
    ]


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:acme/demo.git",
        "https://github.com/acme/demo.git",
        "ssh://git@github.com/acme/demo",
        "https://user@github.com/Acme/Demo/",
    ],
)
def test_remote_forms_normalize_to_one_identity(url: str) -> None:
    assert sessions.normalize_remote(url) == "github.com/acme/demo"


def test_a_session_whose_workspace_is_gone_is_attributed_by_its_remote(
    repo: Path, tmp_path: Path
) -> None:
    git("-C", str(repo), "remote", "add", "origin", "git@github.com:acme/demo.git")
    root = tmp_path / "codex"
    record = {"type": "session_meta", "timestamp": "2026-10-01T10:00:00Z"}
    write_jsonl(
        root / "a" / "rollout-1.jsonl", record | {"payload": {"id": "live", "cwd": str(repo)}}
    )
    write_jsonl(
        root / "a" / "rollout-2.jsonl",
        record
        | {
            "payload": {
                "id": "moved",
                "cwd": "/gone/demo",
                "git": {"repository_url": "https://github.com/acme/demo"},
            }
        },
    )
    write_jsonl(
        root / "a" / "rollout-3.jsonl", record | {"payload": {"id": "live", "cwd": "/gone/other"}}
    )
    codex = store(
        "codex",
        root,
        match={"type": "session_meta"},
        session_id="payload.id",
        cwd="payload.cwd",
        max_lines=1,
        remote="payload.git.repository_url",
    )
    executed: list[str] = []

    result = sessions.resolve(recorder(executed), stores=[codex], state=tmp_path / "scan.json")

    rows = {row["session_id"]: row for row in rows_of(executed)}
    assert result == {"sessions": 2, "attributed": 2}
    assert (rows["moved"]["project_name"], rows["moved"]["resolved_by"]) == ("demo", "remote")
    assert (rows["live"]["status"], rows["live"]["resolved_by"]) == ("attributed", "workspace")
    saved = json.loads((tmp_path / "scan.json").read_text())
    assert saved["remotes"] == {"github.com/acme/demo": str(repo.resolve())}

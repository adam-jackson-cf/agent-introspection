import hashlib
import json
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from agent_introspection import sessions, spool


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


def spooled(inbox: Path) -> list[dict[str, Any]]:
    return [json.loads(path.read_text()) for path in sorted(inbox.glob("*.json"))]


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
    inbox = tmp_path / "spool"

    first = sessions.resolve(stores=stores, state=state, inbox=inbox)

    assert first == {"sessions": 1, "attributed": 1}
    (row,) = spooled(inbox)
    assert (row["session_id"], row["store"], row["project_name"]) == ("s1", "claude-code", "demo")
    assert row["schema"] == spool.SESSION_PROJECT_SCHEMA
    assert row["event_id"] + ".json" in {path.name for path in inbox.iterdir()}
    assert row["resolved_at"].endswith("Z")
    assert "prompt" not in json.dumps(row)

    write_jsonl(
        pending, {"sessionId": "s2", "cwd": "/nowhere", "timestamp": "2026-10-01T11:00:00Z"}
    )
    again = tmp_path / "again"
    second = sessions.resolve(stores=stores, state=state, inbox=again)

    assert second == {"sessions": 1, "missing_workspace": 1}
    assert [row["session_id"] for row in spooled(again)] == ["s2"]
    assert sessions.resolve(stores=stores, state=state, rescan=True, inbox=again)["sessions"] == 2


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
    inbox = tmp_path / "spool"

    result = sessions.resolve(stores=[codex], state=tmp_path / "scan.json", inbox=inbox)

    rows = {row["session_id"]: row for row in spooled(inbox)}
    assert result == {"sessions": 2, "attributed": 2}
    assert (rows["moved"]["project_name"], rows["moved"]["resolved_by"]) == ("demo", "remote")
    assert (rows["live"]["status"], rows["live"]["resolved_by"]) == ("attributed", "workspace")
    assert rows["moved"]["project_id"] == rows["live"]["project_id"]
    assert rows["moved"]["remote"] == "github.com/acme/demo"
    saved = json.loads((tmp_path / "scan.json").read_text())
    assert saved["remotes"] == {"github.com/acme/demo": str(repo.resolve())}


def clone(tmp_path: Path, name: str, remote: str) -> Path:
    root = tmp_path / name / "checkout"
    root.mkdir(parents=True)
    git("init", "-q", str(root))
    git("-C", str(root), "remote", "add", "origin", remote)
    return root


def test_a_remote_keyed_project_has_one_id_across_checkouts_and_hosts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    first = clone(tmp_path, "one", "git@github.com:acme/demo.git")
    second = clone(tmp_path, "two", "https://github.com/acme/demo")
    elsewhere = clone(tmp_path, "three", "https://github.com/acme/other")

    ids = {sessions.resolve_project(str(root)).project_id for root in (first, second)}
    monkeypatch.setattr("agent_introspection.spool.host_name", lambda: "omarchy")
    ids.add(sessions.resolve_project(str(first)).project_id)

    assert ids == {hashlib.sha256(b"git-remote\0github.com/acme/demo").hexdigest()}
    other = sessions.resolve_project(str(elsewhere))
    assert other.project_id not in ids
    assert sessions.resolve_project(str(first)).remote == "github.com/acme/demo"


@pytest.mark.parametrize(
    "url",
    ["/Users/me/bare.git", "file:///home/me/bare.git", "../bare", "git@host:me", "localhost/x"],
)
def test_an_unsafe_remote_is_dropped_and_the_project_is_keyed_by_host_and_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, url: str
) -> None:
    root = clone(tmp_path, "local", url)
    monkeypatch.setattr("agent_introspection.spool.host_name", lambda: "mac")

    project = sessions.resolve_project(str(root))

    real = os.path.realpath(root)
    assert project.remote == ""
    assert project.project_id == hashlib.sha256(f"git\0mac\0{real}".encode()).hexdigest()


@pytest.mark.parametrize(
    ("root", "expected"),
    [("/Users/me/p", "~/p"), ("/home/adam-ai/p", "~/p"), ("/root", "~"), ("/root/p", "~/p")],
)
def test_project_roots_redact_every_home_form(root: str, expected: str) -> None:
    assert sessions._HOME.sub("~", root) == expected


def test_a_root_directory_that_only_starts_with_root_is_not_a_home() -> None:
    assert sessions._HOME.sub("~", "/rootfs/x") == "/rootfs/x"

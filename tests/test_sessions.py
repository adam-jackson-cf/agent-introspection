import hashlib
import json
import os
import re
import sqlite3
import subprocess
from collections.abc import Callable, Mapping
from contextlib import closing
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
        "https://user:pass@github.com/acme/demo.git",
        "https://github.com/acme/demo.git?access_token=secret",
        "https://github.com/acme/demo.git#token",
        "ssh://git@github.com/acme/demo.git?y=1",
        "git@github.com:acme/demo.git#frag",
        "https://github.com/acme/demo?x=a@b",
    ],
)
def test_remote_forms_normalize_to_one_identity(url: str) -> None:
    assert sessions.normalize_remote(url) == "github.com/acme/demo"


@pytest.mark.parametrize("remote", ["github.com/acme/demo?token=s", "github.com/acme/demo#s"])
def test_safe_remote_rejects_query_and_fragment(remote: str) -> None:
    assert sessions.safe_remote(remote) == ""


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


# opencode keeps its sessions in one SQLite database: table `session`, with the working
# directory in `directory` and the creation time in integer milliseconds.
OPENCODE_QUERY = (
    "SELECT id AS session_id, directory AS cwd, "
    "strftime('%Y-%m-%dT%H:%M:%fZ', time_created / 1000.0, 'unixepoch') AS started_at "
    "FROM session"
)
CREATED_MS = 1_790_000_001_500
CREATED_AT = "2026-09-21T14:13:21.500Z"


def opencode_database(path: Path, *rows: tuple[str, str | None, int], wal: bool = False) -> Path:
    """Create (or add rows to) an opencode-shaped database, closed when it returns."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        if wal:
            connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS session"
            " (id TEXT PRIMARY KEY, directory TEXT, time_created INTEGER)"
        )
        connection.executemany("INSERT INTO session VALUES (?, ?, ?)", rows)
        connection.commit()
    return path


def sqlite_store(*roots: Path | str, **overrides: Any) -> sessions.Store:
    fields: dict[str, Any] = {
        "name": "opencode",
        "harnesses": ("opencode",),
        "roots": tuple(str(root) for root in roots),
        "format": sessions.StoreFormat.SQLITE,
        "query": OPENCODE_QUERY,
    }
    fields.update(overrides)
    return sessions.Store(**fields)


def test_a_sqlite_store_attributes_each_session_once_and_rescan_rereads_them(
    repo: Path, tmp_path: Path
) -> None:
    database = opencode_database(
        tmp_path / "opencode" / "opencode.db",
        ("ses_1", str(repo), CREATED_MS),
        ("ses_2", "/nowhere", CREATED_MS),
        wal=True,
    )
    stores = [sqlite_store(database)]
    state = tmp_path / "scan.json"

    first = sessions.resolve(stores=stores, state=state, inbox=tmp_path / "first")

    assert first == {"sessions": 2, "attributed": 1, "missing_workspace": 1}
    rows = {row["session_id"]: row for row in spooled(tmp_path / "first")}
    assert (rows["ses_1"]["store"], rows["ses_1"]["project_name"]) == ("opencode", "demo")
    assert rows["ses_2"]["started_at"] == CREATED_AT
    assert sessions.resolve(stores=stores, state=state, inbox=tmp_path / "second") == {
        "sessions": 0
    }

    opencode_database(database, ("ses_3", str(repo), CREATED_MS))
    third = sessions.resolve(stores=stores, state=state, inbox=tmp_path / "third")

    assert third == {"sessions": 1, "attributed": 1}
    assert [row["session_id"] for row in spooled(tmp_path / "third")] == ["ses_3"]
    saved = json.loads(state.read_text())
    assert saved["database_sessions"] == {str(database): ["ses_1", "ses_2", "ses_3"]}
    rescanned = sessions.resolve(stores=stores, state=state, rescan=True, inbox=tmp_path / "all")
    assert rescanned["sessions"] == 3
    # The reads left the closed WAL database exactly as its harness left it.
    assert sorted(os.listdir(database.parent)) == ["opencode.db"]


def test_a_session_row_without_a_working_directory_is_read_once_it_has_one(
    tmp_path: Path,
) -> None:
    database = opencode_database(tmp_path / "opencode.db", ("ses_1", None, CREATED_MS))
    stores = [sqlite_store(database)]
    state = tmp_path / "scan.json"

    assert sessions.resolve(stores=stores, state=state, inbox=tmp_path / "a") == {"sessions": 0}

    with closing(sqlite3.connect(database)) as connection:
        connection.execute("UPDATE session SET directory = '/nowhere'")
        connection.commit()
    later = sessions.resolve(stores=stores, state=state, inbox=tmp_path / "b")

    assert later == {"sessions": 1, "missing_workspace": 1}


def test_a_running_harness_sessions_still_in_the_write_ahead_log_are_read(
    tmp_path: Path,
) -> None:
    database = opencode_database(tmp_path / "opencode.db", ("ses_1", "/a", CREATED_MS), wal=True)
    with closing(sqlite3.connect(database)) as harness:
        harness.execute("PRAGMA wal_autocheckpoint = 0")
        harness.execute("INSERT INTO session VALUES ('ses_2', '/b', ?)", (CREATED_MS,))
        harness.commit()

        found = sessions.read_database(database, sqlite_store(database))

    assert set(found) == {
        sessions.Session("ses_1", "/a", CREATED_AT),
        sessions.Session("ses_2", "/b", CREATED_AT),
    }


def corrupt(path: Path) -> None:
    path.write_bytes(b"not a database" * 100)


def without_table(path: Path) -> None:
    path.write_bytes(b"")


def with_session(path: Path) -> None:
    opencode_database(path, ("ses_bad", "/nowhere", CREATED_MS))


WRITING_QUERY = (
    "INSERT INTO session VALUES ('ses_new', '/x', 0)"
    " RETURNING id AS session_id, directory AS cwd, '' AS started_at"
)


@pytest.mark.parametrize(
    ("prepare", "query", "error"),
    [
        (corrupt, OPENCODE_QUERY, "file is not a database"),
        (without_table, OPENCODE_QUERY, "no such table: session"),
        (with_session, "SELECT id AS session_id FROM session", "no cwd, started_at column"),
        (with_session, WRITING_QUERY, "attempt to write a readonly database"),
    ],
    ids=["corrupt", "no-table", "missing-columns", "writing-query"],
)
def test_an_unreadable_database_is_reported_and_the_scan_goes_on(
    tmp_path: Path, prepare: Callable[[Path], None], query: str, error: str
) -> None:
    bad = tmp_path / "bad" / "opencode.db"
    bad.parent.mkdir()
    prepare(bad)
    before = bad.read_bytes()
    good = opencode_database(tmp_path / "good" / "opencode.db", ("ses_good", "/a", CREATED_MS))
    projects = tmp_path / "projects"
    write_jsonl(projects / "a" / "one.jsonl", {"sessionId": "s1", "cwd": "/b"})
    stores = [sqlite_store(bad, query=query), sqlite_store(good), store("claude-code", projects)]
    state = tmp_path / "scan.json"

    result = sessions.resolve(stores=stores, state=state, inbox=tmp_path / "spool")

    assert result["sessions"] == result["missing_workspace"] == 2
    (failure,) = result["unreadable"]
    assert (failure["store"], failure["database"]) == (
        "opencode",
        sessions._HOME.sub("~", str(bad)),
    )
    assert error in failure["error"]
    assert set(json.loads(state.read_text())["database_sessions"]) == {str(good)}
    assert bad.read_bytes() == before
    assert os.listdir(bad.parent) == ["opencode.db"]


def test_a_locked_database_is_reported_then_read_on_the_next_scan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sessions, "_SQLITE_TIMEOUT_SECONDS", 0.05)
    database = opencode_database(tmp_path / "opencode.db", ("ses_1", "/nowhere", CREATED_MS))
    stores = [sqlite_store(database)]
    state = tmp_path / "scan.json"

    with closing(sqlite3.connect(database, isolation_level=None)) as harness:
        harness.execute("BEGIN EXCLUSIVE")
        locked = sessions.resolve(stores=stores, state=state, inbox=tmp_path / "locked")
        harness.execute("ROLLBACK")

    assert locked["sessions"] == 0
    assert [failure["error"] for failure in locked["unreadable"]] == ["database is locked"]
    free = sessions.resolve(stores=stores, state=state, inbox=tmp_path / "free")
    assert free == {"sessions": 1, "missing_workspace": 1}


def test_a_database_that_disappears_is_not_created(tmp_path: Path) -> None:
    missing = tmp_path / "opencode.db"
    with pytest.raises(sqlite3.OperationalError, match="unable to open database file"):
        sessions.read_database(missing, sqlite_store(missing))
    assert list(tmp_path.iterdir()) == []


def test_sqlite_roots_name_database_files_once_each(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database = opencode_database(tmp_path / "a" / "opencode.db")
    (tmp_path / "b" / "opencode.db").mkdir(parents=True)
    monkeypatch.delenv("INTROSPECTION_TEST_UNSET", raising=False)
    found = sessions.session_files(
        sqlite_store(tmp_path / "*" / "opencode.db", database, "$INTROSPECTION_TEST_UNSET/x.db")
    )
    assert list(found) == [database]


def test_a_scan_state_from_before_sqlite_stores_keeps_its_files(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    done = write_jsonl(projects / "a" / "one.jsonl", {"sessionId": "s1", "cwd": "/b"})
    state = tmp_path / "scan.json"
    state.write_text(json.dumps({"done": [str(done)], "remotes": {}}))

    result = sessions.resolve(stores=[store("claude-code", projects)], state=state, inbox=tmp_path)

    assert result == {"sessions": 0}


SQLITE_ENTRY: dict[str, Any] = {
    "name": "opencode",
    "harnesses": ["opencode"],
    "roots": ["~/.local/share/opencode/opencode.db"],
    "format": "sqlite",
    "query": OPENCODE_QUERY,
}
JSONL_ENTRY: dict[str, Any] = {
    "name": "omp",
    "harnesses": ["oh-my-pi"],
    "roots": ["~/.omp/agent/sessions"],
    "glob": "*/*.jsonl",
    "max_lines": 5,
    "match": {"type": "session"},
    "session_id": "id",
    "cwd": "cwd",
    "started_at": "timestamp",
}


def test_parse_store_builds_each_format_with_extra_roots() -> None:
    sqlite = sessions.parse_store(SQLITE_ENTRY, ["/data/opencode.db"])
    assert sqlite == sessions.Store(
        name="opencode",
        harnesses=("opencode",),
        roots=("~/.local/share/opencode/opencode.db", "/data/opencode.db"),
        format=sessions.StoreFormat.SQLITE,
        query=OPENCODE_QUERY,
    )
    jsonl = sessions.parse_store(JSONL_ENTRY | {"format": "jsonl", "remote": "git.url"})
    assert jsonl == sessions.Store(
        name="omp",
        harnesses=("oh-my-pi",),
        roots=("~/.omp/agent/sessions",),
        glob="*/*.jsonl",
        max_lines=5,
        match={"type": "session"},
        session_id="id",
        cwd="cwd",
        started_at="timestamp",
        remote="git.url",
    )
    assert next(entry for entry in sessions.load_stores() if entry.name == "omp") == (
        sessions.parse_store(JSONL_ENTRY)
    )


def without(entry: Mapping[str, Any], key: str) -> dict[str, Any]:
    return {name: value for name, value in entry.items() if name != key}


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (["opencode"], "a session store must be a table"),
        (without(SQLITE_ENTRY, "name"), "session store: name must be a non-empty string"),
        (SQLITE_ENTRY | {"format": "csv"}, "format must be one of jsonl, sqlite"),
        (without(SQLITE_ENTRY, "query"), "'opencode': missing query"),
        (SQLITE_ENTRY | {"query": " "}, "query must be a non-empty string"),
        (SQLITE_ENTRY | {"glob": "*.db"}, "glob not used by a sqlite store"),
        (SQLITE_ENTRY | {"remote": "repo"}, "remote not used by a sqlite store"),
        (SQLITE_ENTRY | {"harnesses": "opencode"}, "harnesses must be a non-empty list"),
        (SQLITE_ENTRY | {"roots": []}, "roots must be a non-empty list"),
        (without(JSONL_ENTRY, "glob"), "'omp': missing glob"),
        (JSONL_ENTRY | {"query": OPENCODE_QUERY}, "query not used by a jsonl store"),
        (JSONL_ENTRY | {"max_lines": 0}, "max_lines must be a positive integer"),
        (JSONL_ENTRY | {"max_lines": True}, "max_lines must be a positive integer"),
        (JSONL_ENTRY | {"match": "session"}, "match must be a table"),
        (JSONL_ENTRY | {"cwd": ""}, "cwd must be a non-empty string"),
        (JSONL_ENTRY | {"remote": ""}, "remote must be a non-empty string"),
    ],
)
def test_parse_store_rejects_a_malformed_entry(entry: Any, message: str) -> None:
    with pytest.raises(sessions.SessionStoreError, match=re.escape(message)):
        sessions.parse_store(entry)

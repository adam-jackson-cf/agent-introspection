from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from agent_introspection import hooks, projects
from tests.test_projects import recorder

SCRIPTS = Path(__file__).parents[1] / ".agents/skills/introspection-onboarding/scripts"
NOW = datetime(2026, 9, 30, 12, 0, 0, 123456, tzinfo=UTC)
PROMPT = "please fix the flaky login test, my token=supersecret123 is in .env"
SESSION = "sess-1"


def context(tmp_path: Path) -> hooks.Context:
    return hooks.Context(state_dir=tmp_path / "state", now=lambda: NOW)


def tool_call(tool_name: str, tool_input: dict[str, Any], cwd: str = "/Users/me/p") -> hooks.Attrs:
    envelope = {
        "session_id": SESSION,
        "tool_use_id": "tu-1",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "cwd": cwd,
    }
    (item,) = hooks.normalize("claude-code", "PreToolUse", envelope, context(Path("/nonexistent")))
    assert item["event_type"] == "tool_call"
    return dict(item["attrs"])


# --- Parity with select_logs.sql --------------------------------------------------


def test_subcommand_heads_match_the_log_projection() -> None:
    sql = (Path(hooks.__file__).parent / "facts_sql" / "select_logs.sql").read_text()
    listed = re.search(r"command_head IN \(([^)]*)\)", sql)
    assert listed is not None
    assert set(re.findall(r"'([^']+)'", listed[1])) == set(hooks.SUBCOMMAND_HEADS)


@pytest.mark.parametrize(
    ("command", "head", "sub"),
    [
        ("git commit -m wip", "git", "commit"),
        ("FOO=1 BAR=x /usr/local/bin/uv run pytest", "uv", "run"),
        ("./scripts/run.sh --fast", "run.sh", ""),
        ("ls -la", "ls", ""),
        ("npm Install", "npm", ""),
        ("echo word", "echo", ""),
        ("for f in a b", "for", ""),
        ("", "", ""),
    ],
)
def test_command_head_and_sub_skip_env_assignments(command: str, head: str, sub: str) -> None:
    attrs = tool_call("Bash", {"command": command})
    assert (attrs["command_head"], attrs["command_sub"]) == (head, sub)


def test_codex_cmd_argument_is_a_command_too() -> None:
    attrs = tool_call("exec_command", {"cmd": "HUSKY=0 git push", "workdir": "/Users/me/repo"})
    assert (attrs["command_head"], attrs["command_sub"]) == ("git", "push")
    assert attrs["gate_bypass"] == 1
    assert attrs["workdir"] == "~/repo"


def test_targets_from_edit_write_path_patch_and_command_arguments() -> None:
    assert tool_call("Edit", {"file_path": "/Users/me/p/src/a.py", "old_string": "x"})[
        "targets"
    ] == ["~/p/src/a.py"]
    assert tool_call("read", {"path": "notes.md"})["targets"] == ["notes.md"]
    patch = "*** Begin Patch\n*** Update File: src/b.ts\n@@\n*** Add File: /Users/me/c.md\n"
    assert tool_call("apply_patch", {"input": patch})["targets"] == ["src/b.ts", "~/c.md"]
    command = "cat '/Users/me/x.txt' src/lib 1.2 -v README.md, plain"
    assert tool_call("Bash", {"command": command})["targets"] == ["~/x.txt", "src/lib", "README.md"]


def test_targets_are_distinct_and_capped() -> None:
    files = " ".join(f"f{index}.py" for index in range(30))
    targets = tool_call("Bash", {"command": f"ruff check a.py a.py {files}"})["targets"]
    assert targets[:2] == ["a.py", "f0.py"]
    assert len(targets) == hooks.MAX_TARGETS


@pytest.mark.parametrize(
    ("command", "bypass"),
    [
        ("git commit --no-verify -m x", 1),
        ("HUSKY=0 git commit", 1),
        ("env SKIP=ruff git commit", 1),
        ("git commit --no-gpg-sign", 1),
        ("git commit -m 'MYSKIP=1'", 0),
        ("git commit -m ok", 0),
    ],
)
def test_gate_bypass_variants(command: str, bypass: int) -> None:
    assert tool_call("Bash", {"command": command})["gate_bypass"] == bypass


def test_arguments_hash_is_canonical_and_workdir_falls_back_to_cwd() -> None:
    first = tool_call("Bash", {"command": "ls", "description": "list"})
    second = tool_call("Bash", {"description": "list", "command": "ls"})
    assert first["arguments_hash"] == second["arguments_hash"]
    assert len(first["arguments_hash"]) == 16
    assert first["arguments_length"] == len('{"command":"ls","description":"list"}')
    assert first["workdir"] == "~/p"


@pytest.mark.parametrize(
    ("output", "signature"),
    [
        (
            "Traceback (most recent call last):\n"
            '  File "/Users/me/p/a.py", line 3, in <module>\n'
            "    import foo\n"
            "ModuleNotFoundError: No module named 'foo'\n",
            "ModuleNotFoundError: No module named 'foo'",
        ),
        (
            "node:internal/modules/run_main:123\n"
            "    triggerUncaughtException(\n"
            "    ^\n\n"
            "Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'zod' imported from "
            "/Users/me/p/x.mjs\n"
            "    at packageResolve (node:internal/modules/esm/resolve:854:9)\n"
            "Node.js v22.3.0\n",
            "Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'zod' imported from ~/p/x.mjs",
        ),
        (
            "Script failed\nPermission denied: /Users/me/secret/dir\n",
            "Permission denied: ~/secret/dir",
        ),
        (
            "============ test session starts ============\n"
            "=================== FAILURES ===================\n"
            "____ test_login ____\n"
            "E       assert 1 == 2\n"
            "tests/test_login.py:42: AssertionError\n"
            "FAILED tests/test_login.py::test_login - assert 1 == 2\n",
            "tests/test_login.py:N: AssertionError",
        ),
        ("Exit code 1\nTraceback (most recent call last):\n", "Traceback (most recent call last):"),
        ("Exit code 2\nsome plain output\n", ""),
        # Mirrors the SQL: `0x` splits the run, so hex after it becomes a second `N`.
        ("build error 0xdeadbeef00 at step 12\n", "build error NxN at step N"),
        ("x" * 200 + " error: boom", "error: boom"),
    ],
)
def test_failure_signature_prefers_the_most_specific_line(output: str, signature: str) -> None:
    assert hooks.failure_signature(output) == signature


def test_tool_failure_record(tmp_path: Path) -> None:
    envelope = {
        "session_id": SESSION,
        "tool_use_id": "tu-9",
        "tool_name": "Bash",
        "tool_input": {"command": "pytest"},
        "error": "Exit code 1\nValueError: bad 42",
        "is_interrupt": True,
    }
    (item,) = hooks.normalize("claude-code", "PostToolUseFailure", envelope, context(tmp_path))
    assert item["attrs"] == {
        "tool_use_id": "tu-9",
        "tool_name": "Bash",
        "failure_signature": "ValueError: bad N",
        "interrupted": 1,
    }


# --- Prompts ----------------------------------------------------------------------


def test_prompt_is_measured_never_stored(tmp_path: Path) -> None:
    envelope = {"session_id": SESSION, "prompt_id": "p-1", "prompt": PROMPT, "cwd": "/x"}

    (submitted,) = hooks.normalize("claude-code", "UserPromptSubmit", envelope, context(tmp_path))

    assert submitted["event_type"] == "prompt_submitted"
    assert submitted["occurred_at"] == "2026-09-30T12:00:00.123456Z"
    assert submitted["attrs"] == {"prompt_id": "p-1", "prompt_length": len(PROMPT), "turn_open": 0}
    assert "flaky" not in json.dumps(submitted)


def test_turn_open_tracks_prompt_and_stop(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    envelope = {"session_id": SESSION, "prompt_id": "p-1", "prompt": "go"}
    first = hooks.normalize("claude-code", "UserPromptSubmit", envelope, ctx)[0]
    second = hooks.normalize(
        "claude-code", "UserPromptSubmit", envelope | {"prompt_id": "p-2"}, ctx
    )[0]
    assert (first["attrs"]["turn_open"], second["attrs"]["turn_open"]) == (0, 1)
    hooks.normalize(
        "claude-code", "StopFailure", {"session_id": SESSION, "error": "rate_limit"}, ctx
    )
    third = hooks.normalize("claude-code", "UserPromptSubmit", envelope, ctx)[0]
    assert third["attrs"]["turn_open"] == 0
    assert hooks.turn_open(ctx, "claude-code", SESSION) == 1
    markers = list((tmp_path / "state").iterdir())
    assert all("go" not in path.read_text() for path in markers)


# --- Claude Stop and the other events ---------------------------------------------


def transcript(tmp_path: Path) -> Path:
    def assistant(message_id: str, model: str, thinking: int) -> dict[str, Any]:
        usage = {"output_tokens": 9, "output_tokens_details": {"thinking_tokens": thinking}}
        return {
            "type": "assistant",
            "message": {"id": message_id, "model": model, "usage": usage, "content": []},
        }

    lines = [
        {"type": "user", "promptId": "p-0", "message": {"role": "user", "content": "old"}},
        assistant("m0", "claude-old", 500),
        {"type": "user", "promptId": "p-1", "message": {"role": "user", "content": "new"}},
        assistant("m1", "claude-opus-5-5", 100),
        assistant("m1", "claude-opus-5-5", 120),
        {
            "type": "user",
            "promptId": "p-1",
            "toolUseResult": {},
            "message": {"role": "user", "content": [{"type": "tool_result"}]},
        },
        {**assistant("side", "claude-haiku", 999), "isSidechain": True},
        assistant("m2", "<synthetic>", 0),
        assistant("m3", "claude-opus-5-5", 30),
    ]
    path = tmp_path / "t.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\nnot json\n")
    return path


def test_stop_sums_thinking_tokens_of_the_turn(tmp_path: Path) -> None:
    envelope = {
        "session_id": SESSION,
        "prompt_id": "p-1",
        "transcript_path": str(transcript(tmp_path)),
    }
    (item,) = hooks.normalize("claude-code", "Stop", envelope, context(tmp_path))
    assert item["event_type"] == "turn_stop"
    assert item["attrs"] == {
        "prompt_id": "p-1",
        "reasoning_tokens": 150.0,
        "response_model": "claude-opus-5-5",
    }


def test_stop_without_a_known_prompt_id_uses_the_last_typed_prompt(tmp_path: Path) -> None:
    usage = hooks.transcript_usage(transcript(tmp_path), "missing")
    assert usage == (150.0, "claude-opus-5-5")


def test_claude_failure_and_subagent_events(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    (failure,) = hooks.normalize(
        "claude-code",
        "StopFailure",
        {"session_id": SESSION, "prompt_id": "p", "error": "rate_limit", "error_details": "x y"},
        ctx,
    )
    assert failure["attrs"] == {"prompt_id": "p", "error_class": "rate_limit"}
    (weird,) = hooks.normalize(
        "claude-code", "StopFailure", {"session_id": SESSION, "error": "free text here"}, ctx
    )
    assert weird["attrs"]["error_class"] == "unknown"
    (agent,) = hooks.normalize(
        "claude-code",
        "SubagentStart",
        {"session_id": SESSION, "agent_id": "a1", "agent_type": "Explore", "prompt_id": "p"},
        ctx,
    )
    assert agent["attrs"] == {"agent_id": "a1", "agent_type": "Explore", "prompt_id": "p"}


def test_omp_events(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    base = {"session_id": SESSION, "cwd": "/Users/me/p"}
    (call,) = hooks.normalize(
        "omp",
        "tool_call",
        base | {"tool_use_id": "c1", "tool_name": "bash", "tool_input": {"command": "git status"}},
        ctx,
    )
    assert (call["attrs"]["command_head"], call["attrs"]["command_sub"]) == ("git", "status")
    (approval,) = hooks.normalize(
        "omp",
        "tool_approval_resolved",
        base | {"tool_use_id": "c1", "tool_name": "bash", "approved": False},
        ctx,
    )
    assert approval["attrs"] == {"tool_use_id": "c1", "tool_name": "bash", "approved": 0}
    (start,) = hooks.normalize("omp", "auto_retry_start", base | {"attempt": 2}, ctx)
    (end,) = hooks.normalize("omp", "auto_retry_end", base | {"attempt": 2, "success": True}, ctx)
    assert start["attrs"] == {"attempt": 2, "phase": "start"}
    assert end["attrs"] == {"attempt": 2, "phase": "end", "success": 1}
    (steer,) = hooks.normalize("omp", "input", base | {"text": PROMPT, "idle": False}, ctx)
    assert steer["attrs"] == {"prompt_length": len(PROMPT)}
    assert PROMPT not in json.dumps(steer)
    assert hooks.normalize("omp", "input", base | {"text": PROMPT, "idle": True}, ctx) == []


def test_unknown_producer_event_and_missing_session_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="producer"):
        hooks.normalize("gemini", "Stop", {"session_id": SESSION}, context(tmp_path))
    # Codex needs no hook: its prompts reach the classifier through its own telemetry.
    with pytest.raises(ValueError, match="producer"):
        hooks.normalize("codex", "UserPromptSubmit", {"session_id": SESSION}, context(tmp_path))
    with pytest.raises(ValueError, match="unsupported"):
        hooks.normalize("claude-code", "Notification", {"session_id": SESSION}, context(tmp_path))
    with pytest.raises(ValueError, match="session_id"):
        hooks.normalize("claude-code", "PreToolUse", {}, context(tmp_path))


def test_no_raw_argument_or_output_text_in_records(tmp_path: Path) -> None:
    secret = "printf '%s' \"Hunter2 password value\" > /tmp/leak"
    ctx = context(tmp_path)
    records = hooks.normalize(
        "claude-code",
        "PreToolUse",
        {"session_id": SESSION, "tool_name": "Bash", "tool_input": {"command": secret}},
        ctx,
    ) + hooks.normalize(
        "claude-code",
        "PostToolUseFailure",
        {"session_id": SESSION, "tool_name": "Bash", "error": "hunter2-password-value leaked"},
        ctx,
    )
    serialized = json.dumps(records)
    assert "Hunter2" not in serialized
    assert "hunter2" not in serialized
    assert secret not in serialized


# --- Writing, CLI entry, and ingest -----------------------------------------------


def test_write_is_atomic_and_main_never_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inbox = tmp_path / "inbox"
    monkeypatch.setattr(hooks, "INBOX", inbox)
    monkeypatch.setattr(hooks, "LOG", tmp_path / "hooks.log")
    envelope = {"session_id": SESSION, "tool_name": "Bash", "tool_input": {"command": "ls"}}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(envelope)))
    assert hooks.main("claude-code", "PreToolUse") == 0
    (written,) = inbox.iterdir()
    assert json.loads(written.read_text())["schema"] == hooks.SCHEMA
    assert written.name == json.loads(written.read_text())["event_id"] + ".json"

    monkeypatch.setattr("sys.stdin", io.StringIO('{"prompt": "' + PROMPT + '"'))
    assert hooks.main("claude-code", "UserPromptSubmit") == 0
    log = (tmp_path / "hooks.log").read_text()
    assert "claude-code UserPromptSubmit JSONDecodeError" in log
    assert PROMPT not in log


def test_sync_ingests_hook_events_into_their_table(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    ctx = context(tmp_path)
    envelope = {
        "session_id": SESSION,
        "tool_use_id": "t",
        "tool_name": "Edit",
        "tool_input": {"file_path": "/Users/me/p/a.py"},
    }
    (item,) = hooks.normalize("claude-code", "PreToolUse", envelope, ctx)
    hooks.write(item, inbox)
    executed: list[str] = []

    result = projects.sync(recorder(executed), inbox=inbox)

    assert result["hook_events"] == 1
    assert result["invalid"] == 0
    (statement,) = executed
    assert statement.startswith("INSERT INTO introspection.hook_events (event_id, producer,")
    # Declared column types: JSON inference would read the attrs objects as tuples.
    assert "attrs_string Map(String, String), attrs_number Map(String, Float64)" in statement
    assert "SELECT event_id, producer," in statement
    literal = statement.split("Float64)', '", 1)[1].rsplit("') SETTINGS", 1)[0]
    row = json.loads(literal.replace("\\'", "'").replace("\\\\", "\\"))
    assert row["attrs_string"]["targets"] == '["~/p/a.py"]'
    assert row["attrs_number"]["gate_bypass"] == 0
    assert row["attrs_string"]["tool_name"] == "Edit"
    assert "arguments_length" in row["attrs_number"]
    assert list(inbox.iterdir()) == []


def test_large_rows_are_split_by_size() -> None:
    rows = [{"event_id": str(index), "blob": "x" * 5000} for index in range(100)]
    statements = projects.insert_statements("hook_events", rows)
    assert len(statements) > 1
    assert all(len(statement) < 256 * 1024 for statement in statements)


# --- Shim and installers ----------------------------------------------------------


def test_shim_hands_stdin_to_a_detached_cli_and_prints_nothing(tmp_path: Path) -> None:
    received = tmp_path / "received"
    fake = tmp_path / "fake-cli"
    fake.write_text(f'#!/bin/sh\ncat > "{received}.part"; mv "{received}.part" "{received}"\n')
    fake.chmod(0o755)
    result = subprocess.run(
        ["/bin/sh", str(SCRIPTS / "activity-shim.sh"), "claude-code", "PreToolUse"],
        input='{"session_id": "s"}',
        capture_output=True,
        text=True,
        env={**os.environ, "AGENT_INTROSPECTION_BIN": str(fake)},
        timeout=10,
        check=False,
    )
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    for _ in range(100):
        if received.exists():
            break
        subprocess.run(["sleep", "0.05"], check=True, timeout=10)
    assert received.read_text() == '{"session_id": "s"}'


def load_script(relative: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(Path(relative).stem, SCRIPTS / relative)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ORCA = {
    "hooks": [
        {"type": "command", "command": "/bin/sh '/Users/me/.orca/agent-hooks/claude-hook.sh'"}
    ]
}


def test_claude_installer_is_idempotent_preserves_others_and_removes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    installer = load_script("adapters/claude-code/install_activity.py")
    settings = tmp_path / "settings.json"
    original = {
        "env": {"A": "1"},
        "hooks": {"PreToolUse": [{"matcher": "*", **ORCA}], "Stop": [ORCA]},
    }
    settings.write_text(json.dumps(original, indent=2) + "\n")
    runtime = tmp_path / "runtime"
    args = ["--settings", str(settings), "--runtime-dir", str(runtime)]

    assert installer.main([*args, "--dry-run"]) == 0
    assert "+" in capsys.readouterr().out
    assert json.loads(settings.read_text()) == original

    installer.main(args)
    once = settings.read_text()
    installer.main(args)
    assert settings.read_text() == once
    hooks_config = json.loads(once)["hooks"]
    assert set(hooks_config) >= set(installer.EVENTS)
    assert hooks_config["PreToolUse"][0] == {"matcher": "*", **ORCA}
    assert hooks_config["Stop"][0] == ORCA
    assert sum(installer.is_owned(entry) for entry in hooks_config["PreToolUse"]) == 1
    assert (runtime / "activity-shim.sh").stat().st_mode & 0o111
    assert len(list(tmp_path.glob("settings.json.bak-*"))) == 1

    installer.main([*args, "--remove"])
    assert json.loads(settings.read_text()) == original
    assert len(list(tmp_path.glob("settings.json.bak-*"))) == 2

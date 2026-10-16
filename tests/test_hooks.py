from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agent_introspection import command_shape, facts, hooks, spool
from agent_introspection.cli import main
from tests.support.otlp_collector import Collector, names, spool_records

RUNTIME = Path(__file__).parents[1] / "src/agent_introspection/hook_runtime"
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


def test_log_projection_renders_the_shared_command_shape_rules() -> None:
    select = facts.render_select("select_logs.sql", "1")

    assert not re.search(r"\{[a-z_]+\}", select)
    for head in command_shape.SUBCOMMAND_HEADS:
        assert f"'{head}'" in select
    assert "AS verification_kinds" in select


@pytest.mark.parametrize(
    ("command", "head", "sub"),
    [
        ("git commit -m wip", "git", "commit"),
        ("FOO=1 BAR=x /usr/local/bin/uv run pytest", "pytest", ""),
        ("cd app && npm run test:unit", "npm", "test:unit"),
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
        "failure_signature_hash": hashlib.sha256(b"ValueError: bad N").hexdigest()[:16],
        "interrupted": 1,
    }


def test_failure_signature_is_capped_at_80_characters() -> None:
    assert hooks.SIGNATURE_CHARS == 80
    signature = hooks.failure_signature("ValueError: " + "x" * 200)
    assert signature == ("ValueError: " + "x" * 200)[:80]
    assert len(signature) == 80


def test_signature_hash_distinguishes_lines_sharing_the_capped_prefix(tmp_path: Path) -> None:
    prefix = "ValueError: " + "x" * 80
    attrs = []
    for suffix in (" alpha", " beta"):
        envelope = {"session_id": SESSION, "tool_use_id": "tu", "error": prefix + suffix}
        (item,) = hooks.normalize("claude-code", "PostToolUseFailure", envelope, context(tmp_path))
        attrs.append(item["attrs"])
    a, b = attrs

    assert a["failure_signature"] == b["failure_signature"]
    assert a["failure_signature_hash"] != b["failure_signature_hash"]
    assert re.fullmatch(r"[0-9a-f]{16}", a["failure_signature_hash"])


def test_signature_hash_covers_the_redacted_line_not_the_raw_text() -> None:
    one = hooks.normalized_failure_line("Error: login failed password=hunterONE at /Users/me/a 12")
    two = hooks.normalized_failure_line("Error: login failed password=hunterTWO at /Users/you/a 99")

    assert one == two == "Error: login failed password=[REDACTED] at ~/a N"
    assert hooks.signature_hash(one) == hooks.signature_hash(two)
    assert hooks.signature_hash(one) == hashlib.sha256(one.encode()).hexdigest()[:16]
    assert hooks.signature_hash("") == ""


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


def test_usage_stops_at_the_next_typed_prompt(tmp_path: Path) -> None:
    path = transcript(tmp_path)
    later = [
        {"type": "user", "promptId": "p-2", "message": {"role": "user", "content": "next"}},
        {
            "type": "assistant",
            "message": {
                "id": "m9",
                "model": "claude-later",
                "usage": {"output_tokens_details": {"thinking_tokens": 700}},
            },
        },
    ]
    with path.open("a") as handle:
        handle.write("".join(json.dumps(line) + "\n" for line in later))

    assert hooks.transcript_usage(path, "p-1") == (150.0, "claude-opus-5-5")


def test_stop_with_a_missing_transcript_still_emits_turn_stop(tmp_path: Path) -> None:
    envelope = {
        "session_id": SESSION,
        "prompt_id": "p-1",
        "transcript_path": str(tmp_path / "gone.jsonl"),
    }
    (item,) = hooks.normalize("claude-code", "Stop", envelope, context(tmp_path))
    assert item["event_type"] == "turn_stop"
    assert item["attrs"] == {"prompt_id": "p-1", "reasoning_tokens": 0.0, "response_model": ""}


@pytest.mark.parametrize(
    "output",
    [
        "Error: auth failed password=hunterTWO token=ghp_abcDEF123",
        "Error: API key sk-secretvalue was rejected",
        "Error: Authorization: Bearer abcdef123456789 denied",
        "Error: AKIAIOSFODNN7EXAMPLE not permitted; api_key: zzzsecret",
    ],
)
def test_failure_signature_never_keeps_credentials(output: str) -> None:
    signature = hooks.failure_signature(output)
    for secret in ("hunterTWO", "ghp_", "sk-secretvalue", "abcdef", "AKIA", "zzzsecret"):
        assert secret not in signature
    assert "[REDACTED]" in signature


@pytest.mark.parametrize(
    ("output", "secret"),
    [
        ('Error: login failed token="quoted s3cretvalue"', "s3cretvalue"),
        ("Error: login failed password='pw with spaces'", "spaces"),
        ('Error: bad request {"api_key": "zzzsecret99"}', "zzzsecret99"),
        ("Error: API key abcd1234efgh5678ijkl was rejected", "abcd1234"),
        ("Error: upstream returned zXy9Wv8Ut7Sr6Qp5On4Mm3 denied", "zXy9Wv8"),
        ("Error: login failed credential=hunterpw", "hunterpw"),
        ("Error: API key abXYcd1 was rejected", "abXYcd"),
        ("Error: private_key: 'BEGINxyz' invalid", "BEGINxyz"),
    ],
)
def test_failure_signature_redacts_quoted_spaced_and_unprefixed_credentials(
    output: str, secret: str
) -> None:
    signature = hooks.failure_signature(output)
    assert secret not in signature
    assert "[REDACTED]" in signature


@pytest.mark.parametrize(
    "output",
    [
        "Error: token expired for request",
        "ValueError: password authentication failed",
        "FileNotFoundError: /Users/me/project/src/agent_introspection/hooks.py missing",
        "Error: AbstractSingletonProxyFactoryBean unavailable",
        "Error: invalid secret length",
        "Error: password incorrect for user",
    ],
)
def test_failure_signature_keeps_ordinary_diagnostics(output: str) -> None:
    signature = hooks.failure_signature(output)
    assert "[REDACTED]" not in signature
    assert signature == hooks._DIGITS.sub("N", hooks._HOME_ANYWHERE.sub("~", output))


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
    failure = base | {
        "tool_use_id": "c1",
        "tool_name": "bash",
        "error": "ModuleNotFoundError: No module named 'x' at /Users/me/p/a.py",
        "exit_code": 1,
    }
    (result,) = hooks.normalize("omp", "tool_result", failure, ctx)
    assert result["event_type"] == "tool_failure"
    assert result["attrs"]["failure_signature"].startswith("ModuleNotFoundError")
    assert result["attrs"]["exit_code"] == 1
    assert "/Users/me" not in json.dumps(result)


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
    attrs = records[0]["attrs"]
    assert (attrs["command_head"], attrs["verification_kinds"], attrs["gate_run"]) == (
        "printf",
        [],
        0,
    )


# --- Writing, CLI entry, and ingest -----------------------------------------------


def test_write_is_atomic_and_main_never_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hook_inbox = tmp_path / "inbox"
    monkeypatch.setattr(hooks, "INBOX", hook_inbox)
    monkeypatch.setattr(hooks, "LOG", tmp_path / "hooks.log")
    envelope = {"session_id": SESSION, "tool_name": "Bash", "tool_input": {"command": "ls"}}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(envelope)))
    assert hooks.main("claude-code", "PreToolUse", tmp_path / "missing.toml") == 0
    (written,) = hook_inbox.iterdir()
    assert json.loads(written.read_text())["schema"] == hooks.SCHEMA
    assert written.name == json.loads(written.read_text())["event_id"] + ".json"

    monkeypatch.setattr("sys.stdin", io.StringIO('{"prompt": "' + PROMPT + '"'))
    assert hooks.main("claude-code", "UserPromptSubmit", tmp_path / "missing.toml") == 0
    log = (tmp_path / "hooks.log").read_text()
    assert "claude-code UserPromptSubmit JSONDecodeError" in log
    assert PROMPT not in log


def test_home_directories_redact_on_every_platform() -> None:
    assert hooks.redact_home("/Users/me/a /home/adam-ai/b /root/c") == "~/a ~/b ~/c"
    assert hooks.redact_home("--dir=/root") == "--dir=~"
    assert hooks.redact_home("/rootfs/x") == "/rootfs/x"
    signature = hooks.failure_signature("FileNotFoundError: /home/adam-ai/p/x.py and /root/y")
    assert "adam-ai" not in signature
    assert "/root" not in signature
    assert signature.startswith("FileNotFoundError: ~/p/x.py and ~")


def hook_config(tmp_path: Path, endpoint: str | None) -> Path:
    config = tmp_path / "config.toml"
    config.write_text("" if endpoint is None else f'[hooks]\notlp_logs_endpoint = "{endpoint}"\n')
    return config


def feed_envelope(monkeypatch: pytest.MonkeyPatch, **fields: object) -> None:
    envelope = {"session_id": SESSION, "tool_name": "Bash", "tool_input": {"command": "ls"}}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(envelope | fields)))


def test_a_hook_drains_one_batch_of_the_spool_and_leaves_the_rest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, inbox: Path, collector: Collector
) -> None:
    spool_records(inbox, 60)
    feed_envelope(monkeypatch)

    assert hooks.main("claude-code", "PreToolUse", hook_config(tmp_path, collector.url)) == 0

    assert len(collector.bodies) == 1
    assert len(collector.records) == spool.BATCH_SIZE
    assert len(names(inbox)) == 61 - spool.BATCH_SIZE


def test_a_hook_without_hooks_config_sends_to_the_collectors_logs_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, inbox: Path, collector: Collector
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(f'[signoz]\notlp_endpoint = "{collector.base}"\n')
    feed_envelope(monkeypatch)

    assert hooks.main("claude-code", "PreToolUse", config) == 0

    assert len(collector.records) == 1
    assert names(inbox) == set()


def test_the_otel_environment_never_redirects_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, inbox: Path, collector: Collector
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", collector.base)
    feed_envelope(monkeypatch)

    assert (
        hooks.main("claude-code", "PreToolUse", hook_config(tmp_path, "http://127.0.0.1:9/v1/logs"))
        == 0
    )

    assert collector.bodies == []
    assert len(names(inbox)) == 1


def test_a_hook_survives_an_unreachable_collector_and_an_invalid_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, inbox: Path
) -> None:
    config = hook_config(tmp_path, "http://127.0.0.1:9/v1/logs")
    feed_envelope(monkeypatch)
    assert hooks.main("claude-code", "PreToolUse", config) == 0
    assert len(names(inbox)) == 1
    assert " spool ConnectionRefusedError 1" in hooks.LOG.read_text()

    config.write_text("[hooks\n")
    feed_envelope(monkeypatch, tool_name="Read")
    assert hooks.main("claude-code", "PreToolUse", config) == 0
    assert " spool ConfigurationError 0" in hooks.LOG.read_text()
    assert len(names(inbox)) == 2


# --- Shim and omp plugin ----------------------------------------------------------


def test_shim_hands_stdin_to_a_detached_cli_and_prints_nothing(tmp_path: Path) -> None:
    received = tmp_path / "received"
    fake = tmp_path / "fake-cli"
    fake.write_text(f'#!/bin/sh\ncat > "{received}.part"; mv "{received}.part" "{received}"\n')
    fake.chmod(0o755)
    result = subprocess.run(
        ["/bin/sh", str(RUNTIME / "activity-shim.sh"), "claude-code", "PreToolUse"],
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


OMP_PROBE = """
import register from "%s";
let sent = 0;
globalThis.fetch = (() => { sent += 1; return Promise.resolve(new Response("")); }) as any;
const handlers: Record<string, any> = {};
register({ on: (name: string, fn: any) => { handlers[name] = fn; } } as any);
handlers.before_agent_start(
  { prompt: "secret prompt" },
  { sessionManager: { getSessionId: () => "s1" } },
);
console.log(sent);
"""


OMP_RESULT_PROBE = """
import register from "%s";
const handlers: Record<string, any> = {};
register({ on: (name: string, fn: any) => { handlers[name] = fn; } } as any);
const context = { sessionManager: { getSessionId: () => "s1" }, cwd: "/Users/me/p" };
handlers.tool_result({ toolCallId: "c0", toolName: "bash", isError: false, content: [] }, context);
handlers.tool_result(
  { toolCallId: "c1", toolName: "bash", isError: undefined, content: [] },
  context,
);
handlers.tool_result(
  {
    toolCallId: "c2",
    toolName: "bash",
    isError: true,
    content: [{ type: "text", text: "boom: ModuleNotFoundError" }, { type: "image" }],
    details: { exitCode: 2 },
  },
  context,
);
await new Promise((resolve) => setTimeout(resolve, 1500));
"""


def test_omp_tool_result_spawns_the_shim_only_for_errors(tmp_path: Path) -> None:
    shim = tmp_path / "shim.sh"
    sink = tmp_path / "sink"
    shim.write_text(f'echo "$2" >> {sink}\ncat >> {sink}\necho >> {sink}\n')
    probe = tmp_path / "probe.ts"
    probe.write_text(OMP_RESULT_PROBE % (RUNTIME / "omp/activity.ts"))
    env = {**os.environ, "AGENT_INTROSPECTION_SHIM": str(shim)}
    subprocess.run(["bun", str(probe)], env=env, capture_output=True, timeout=60, check=True)

    lines = sink.read_text().split()
    assert lines.count("tool_result") == 1
    (envelope,) = [
        json.loads(line) for line in sink.read_text().splitlines() if line.startswith("{")
    ]
    assert envelope["tool_use_id"] == "c2"
    assert envelope["error"] == "boom: ModuleNotFoundError\n"
    assert envelope["exit_code"] == 2


@pytest.mark.parametrize(("opt_in", "expected"), [(None, "0"), ("1", "1")])
def test_omp_prompt_is_exported_only_after_opt_in(
    tmp_path: Path, opt_in: str | None, expected: str
) -> None:
    probe = tmp_path / "probe.ts"
    probe.write_text(OMP_PROBE % (RUNTIME / "omp/activity.ts"))
    env = {
        **os.environ,
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:9",
        "AGENT_INTROSPECTION_SHIM": "/nonexistent",
    }
    env.pop("AGENT_INTROSPECTION_OMP_PROMPT_EXPORT", None)
    if opt_in is not None:
        env["AGENT_INTROSPECTION_OMP_PROMPT_EXPORT"] = opt_in
    result = subprocess.run(
        ["bun", str(probe)], env=env, capture_output=True, text=True, timeout=60, check=True
    )
    assert result.stdout.strip() == expected


def test_every_home_path_in_one_argument_is_redacted() -> None:
    value = "~/.omp/**/x;/Users/someone/.claude/**/x;/Users/someone/.agents/**/x"
    assert hooks.redact_home(value) == "~/.omp/**/x;~/.claude/**/x;~/.agents/**/x"


def test_the_hook_command_help_names_exactly_the_producers_it_accepts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        main(["hook", "--help"])

    lines = capsys.readouterr().out.splitlines()
    producer = next(line.split(None, 1)[1] for line in lines if line.startswith("  producer "))
    assert producer == " or ".join(hooks.PRODUCERS)

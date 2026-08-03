from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ADAPTER = (
    Path(__file__).parents[1]
    / ".agents/skills/introspection-onboarding/scripts/adapters/claude-code.py"
)
RFC3339_UTC = re.compile(rb"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")


def _adapter_with_fake_runtime(tmp_path: Path) -> tuple[Path, Path]:
    scripts = tmp_path / "scripts"
    adapters = scripts / "adapters"
    adapters.mkdir(parents=True)
    adapter = adapters / "claude-code.py"
    shutil.copyfile(ADAPTER, adapter)
    adapter.chmod(0o755)

    captured = tmp_path / "runtime-argv"
    runtime = scripts / "session-context-runtime.sh"
    runtime.write_text('#!/usr/bin/env bash\nprintf "%s\\0" "$@" > "$CAPTURED_ARGV"\n')
    runtime.chmod(0o755)
    return adapter, captured


def _run(adapter: Path, captured: Path, payload: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(adapter)],
        input=payload,
        text=True,
        capture_output=True,
        env={"CAPTURED_ARGV": str(captured)},
        check=False,
    )


@pytest.mark.parametrize(
    ("hook_event_name", "event_type"),
    (("SessionStart", "session_start"), ("SessionEnd", "session_end")),
)
def test_claude_lifecycle_hooks_normalize_to_runtime_argv(
    tmp_path: Path, hook_event_name: str, event_type: str
) -> None:
    adapter, captured = _adapter_with_fake_runtime(tmp_path)
    workspace = (tmp_path / "workspace").as_posix()
    payload = json.dumps(
        {
            "hook_event_name": hook_event_name,
            "session_id": "claude-session",
            "cwd": workspace,
            "transcript_path": "/private/transcript.jsonl",
            "source": "startup",
        }
    )

    result = _run(adapter, captured, payload)

    assert result.returncode == 0
    argv = captured.read_bytes().split(b"\0")[:-1]
    assert argv[:3] == [b"claude-code", b"claude-session", event_type.encode()]
    assert RFC3339_UTC.fullmatch(argv[3])
    assert argv[4] == workspace.encode()


@pytest.mark.parametrize(
    "payload",
    (
        "[]",
        '{"hook_event_name":"Unknown","session_id":"id","cwd":"/workspace"}',
        '{"hook_event_name":"SessionStart","cwd":"/workspace"}',
        '{"hook_event_name":"SessionStart","session_id":"id","cwd":"workspace"}',
        '{"hook_event_name":"SessionEnd","session_id":"id\\nother","cwd":"/workspace"}',
        '{"hook_event_name":"SessionStart","hook_event_name":"SessionEnd","session_id":"id","cwd":"/workspace"}',
        '{"hook_event_name":"SessionStart","session_id":"id","cwd":"/workspace"} trailing',
    ),
)
def test_claude_malformed_hooks_fail_closed_without_runtime_invocation(
    tmp_path: Path, payload: str
) -> None:
    adapter, captured = _adapter_with_fake_runtime(tmp_path)

    result = _run(adapter, captured, payload)

    assert result.returncode == 64
    assert not captured.exists()

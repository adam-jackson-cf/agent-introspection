from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ADAPTER_SOURCE = (
    Path(__file__).parents[1]
    / ".agents/skills/introspection-onboarding/scripts/adapters/codex-cli/adapter.py"
)
APP_SERVER_SOURCE = (
    Path(__file__).parents[1]
    / ".agents/skills/introspection-onboarding/scripts/adapters/codex-app-server/adapter.py"
)


def _adapter(tmp_path: Path) -> tuple[Path, Path]:
    scripts = tmp_path / "scripts"
    adapter_dir = scripts / "adapters" / "codex-cli"
    adapter_dir.mkdir(parents=True)
    adapter = adapter_dir / "adapter.py"
    shutil.copy2(ADAPTER_SOURCE, adapter)
    log = tmp_path / "runtime-arguments.jsonl"
    runtime = scripts / "session-context-runtime.sh"
    runtime.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "import sys\n"
        "with open(os.environ['FAKE_RUNTIME_LOG'], 'a', encoding='utf-8') as output:\n"
        "    json.dump(sys.argv[1:], output)\n"
        "    output.write('\\n')\n",
        encoding="utf-8",
    )
    runtime.chmod(runtime.stat().st_mode | stat.S_IXUSR)
    return adapter, log


def _app_server_adapter(tmp_path: Path) -> tuple[Path, Path]:
    scripts = tmp_path / "scripts"
    adapter_dir = scripts / "adapters" / "codex-app-server"
    adapter_dir.mkdir(parents=True)
    adapter = adapter_dir / "adapter.py"
    shutil.copy2(APP_SERVER_SOURCE, adapter)
    log = tmp_path / "runtime-arguments.jsonl"
    runtime = scripts / "session-context-runtime.sh"
    runtime.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "import sys\n"
        "with open(os.environ['FAKE_RUNTIME_LOG'], 'a', encoding='utf-8') as output:\n"
        "    json.dump(sys.argv[1:], output)\n"
        "    output.write('\\n')\n"
        "raise SystemExit(int(os.environ.get('FAKE_RUNTIME_EXIT', '0')))\n",
        encoding="utf-8",
    )
    runtime.chmod(runtime.stat().st_mode | stat.S_IXUSR)
    return adapter, log


def _invoke(
    adapter: Path, home: Path, log: Path, payload: object
) -> subprocess.CompletedProcess[str]:
    return _invoke_json(adapter, home, log, json.dumps(payload))


def _invoke_json(
    adapter: Path, home: Path, log: Path, payload: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(adapter), payload],
        capture_output=True,
        check=False,
        env={**os.environ, "HOME": str(home), "FAKE_RUNTIME_LOG": str(log)},
        text=True,
    )


def _invoke_app_server(
    adapter: Path,
    home: Path,
    log: Path,
    payload: str,
    *,
    runtime_exit: int = 0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(adapter)],
        input=payload,
        capture_output=True,
        check=False,
        env={
            **os.environ,
            "HOME": str(home),
            "FAKE_RUNTIME_LOG": str(log),
            "FAKE_RUNTIME_EXIT": str(runtime_exit),
        },
        text=True,
    )


def _events(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def test_codex_notify_maps_each_native_notify_to_repeatable_session_context_without_state(
    tmp_path: Path,
) -> None:
    adapter, log = _adapter(tmp_path)
    home = tmp_path / "home"
    first_workspace = tmp_path / "one"
    second_workspace = tmp_path / "two"
    first_workspace.mkdir()
    second_workspace.mkdir()
    first = {
        "type": "agent-turn-complete",
        "thread-id": "thread-42",
        "cwd": str(first_workspace),
        "last-assistant-message": "must not be retained",
    }

    started = datetime.now(UTC)
    assert _invoke(adapter, home, log, first).returncode == 0
    finished = datetime.now(UTC)
    assert _invoke(adapter, home, log, first).returncode == 0
    changed = {**first, "cwd": str(second_workspace), "timestamp": "2026-08-03T12:01:00+00:00"}
    assert _invoke(adapter, home, log, changed).returncode == 0

    events = _events(log)
    assert len(events) == 3
    assert events[0][:3] == ["codex-cli", "thread-42", "session_context"]
    assert started <= datetime.fromisoformat(events[0][3]) <= finished
    assert events[0][4] == str(first_workspace)
    assert events[1][:3] == ["codex-cli", "thread-42", "session_context"]
    assert events[1][4] == str(first_workspace)
    assert events[2] == [
        "codex-cli",
        "thread-42",
        "session_context",
        "2026-08-03T12:01:00+00:00",
        str(second_workspace),
    ]
    assert not (home / ".local/state/agent-introspection/codex-hook").exists()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"type": "agent-turn-complete", "cwd": "/tmp"},
        {"type": "agent-turn-complete", "thread-id": "thread", "cwd": "relative"},
        {
            "type": "agent-turn-complete",
            "thread-id": "thread",
            "cwd": "/tmp",
            "timestamp": "not-a-time",
        },
        {"type": "other", "thread-id": "thread", "cwd": "/tmp"},
    ],
)
def test_codex_notify_rejects_malformed_envelopes_without_runtime_invocation(
    tmp_path: Path, payload: object
) -> None:
    adapter, log = _adapter(tmp_path)

    result = _invoke(adapter, tmp_path / "home", log, payload)

    assert result.returncode == 64
    assert _events(log) == []


@pytest.mark.parametrize(
    "additional_id",
    [{"thread.id": "thread-42"}, {"thread_id": "other-thread"}],
)
def test_codex_notify_rejects_multiple_authoritative_thread_ids(
    tmp_path: Path, additional_id: dict[str, str]
) -> None:
    adapter, log = _adapter(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    payload = {
        "type": "agent-turn-complete",
        "thread-id": "thread-42",
        "cwd": str(workspace),
        "timestamp": "2026-08-03T12:00:00Z",
        **additional_id,
    }

    result = _invoke(adapter, tmp_path / "home", log, payload)

    assert result.returncode == 64
    assert _events(log) == []


@pytest.mark.parametrize(
    "duplicate_field",
    ["type", "thread-id", "cwd", "timestamp"],
)
def test_codex_notify_rejects_duplicate_authoritative_fields_without_runtime_invocation(
    tmp_path: Path, duplicate_field: str
) -> None:
    adapter, log = _adapter(tmp_path)
    home = tmp_path / "home"
    original_workspace = tmp_path / "one"
    changed_workspace = tmp_path / "two"
    original_workspace.mkdir()
    changed_workspace.mkdir()
    first = {
        "type": "agent-turn-complete",
        "thread-id": "thread-42",
        "cwd": str(original_workspace),
        "timestamp": "2026-08-03T12:00:00Z",
    }

    assert _invoke(adapter, home, log, first).returncode == 0

    duplicate_values = {
        "type": '"agent-turn-complete","type":"other"',
        "thread-id": '"thread-42","thread-id":"other-thread"',
        "cwd": f'"{original_workspace}","cwd":"{changed_workspace}"',
        "timestamp": '"2026-08-03T12:01:00Z","timestamp":"2026-08-03T12:02:00Z"',
    }
    malformed = (
        json.dumps(
            {"type": "agent-turn-complete", "thread-id": "thread-42", "cwd": str(changed_workspace)}
        )[:-1]
        + f",{json.dumps(duplicate_field)}:{duplicate_values[duplicate_field]}"
        + "}"
    )

    result = _invoke_json(adapter, home, log, malformed)

    assert result.returncode == 64
    assert _events(log) == [
        [
            "codex-cli",
            "thread-42",
            "session_context",
            "2026-08-03T12:00:00+00:00",
            str(original_workspace),
        ]
    ]


def test_codex_app_server_runs_with_deployed_system_python(tmp_path: Path) -> None:
    system_python = Path("/usr/bin/python3")
    assert system_python.is_file()
    adapter, log = _app_server_adapter(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    payload = json.dumps(
        {
            "hook_event_name": "SessionStart",
            "session_id": "thread-42",
            "cwd": str(workspace),
            "source": "startup",
        }
    )

    result = subprocess.run(
        [str(system_python), str(adapter)],
        input=payload,
        capture_output=True,
        check=False,
        env={
            **os.environ,
            "HOME": str(tmp_path / "home"),
            "FAKE_RUNTIME_LOG": str(log),
            "FAKE_RUNTIME_EXIT": "0",
        },
        text=True,
    )

    assert result.returncode == 0
    assert _events(log)[0][:3] == ["codex-app-server", "thread-42", "session_start"]


@pytest.mark.parametrize("source", ["startup", "resume", "clear", "compact"])
def test_codex_app_server_normalizes_session_start_hook_envelopes(
    tmp_path: Path, source: str
) -> None:
    adapter, log = _app_server_adapter(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    payload = {
        "hook_event_name": "SessionStart",
        "session_id": "thread-42",
        "cwd": str(workspace),
        "source": source,
    }

    started = datetime.now(UTC)
    result = _invoke_app_server(adapter, tmp_path / "home", log, json.dumps(payload))
    finished = datetime.now(UTC)

    assert result.returncode == 0
    event = _events(log)
    assert len(event) == 1
    assert event[0][:3] == ["codex-app-server", "thread-42", "session_start"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)", event[0][3])
    occurred_at = datetime.fromisoformat(event[0][3])
    assert occurred_at.tzinfo == UTC
    assert started <= occurred_at <= finished
    assert event[0][4] == str(workspace)


def test_codex_app_server_normalizes_session_end_hook_envelope(tmp_path: Path) -> None:
    adapter, log = _app_server_adapter(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    started = datetime.now(UTC)
    result = _invoke_app_server(
        adapter,
        tmp_path / "home",
        log,
        json.dumps(
            {
                "hook_event_name": "SessionEnd",
                "session_id": "thread-42",
                "cwd": str(workspace),
                "reason": "other",
            }
        ),
    )
    finished = datetime.now(UTC)

    assert result.returncode == 0
    event = _events(log)
    assert len(event) == 1
    assert event[0][:3] == ["codex-app-server", "thread-42", "session_end"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)", event[0][3])
    occurred_at = datetime.fromisoformat(event[0][3])
    assert occurred_at.tzinfo == UTC
    assert started <= occurred_at <= finished
    assert event[0][4] == str(workspace)


def test_codex_app_server_ignores_unrelated_sensitive_hook_fields_without_accessing_them(
    tmp_path: Path,
) -> None:
    adapter, log = _app_server_adapter(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    unreadable_transcript = tmp_path / "transcript-path-is-a-directory"
    unreadable_transcript.mkdir()

    result = _invoke_app_server(
        adapter,
        tmp_path / "home",
        log,
        json.dumps(
            {
                "hook_event_name": "SessionStart",
                "session_id": "thread-42",
                "cwd": str(workspace),
                "source": "startup",
                "transcript_path": str(unreadable_transcript),
                "prompt": "must not be read",
                "response": "must not be read",
                "arbitrary": {"payload": "must not be read"},
            }
        ),
    )

    assert result.returncode == 0
    event = _events(log)
    assert len(event) == 1
    assert event[0][:3] == ["codex-app-server", "thread-42", "session_start"]
    assert event[0][4] == str(workspace)
    assert all("must not be read" not in argument for argument in event[0])


@pytest.mark.parametrize(
    ("payload", "duplicate_field", "duplicate_value"),
    [
        (
            {
                "hook_event_name": "SessionStart",
                "session_id": "thread-42",
                "cwd": "/tmp",
                "source": "startup",
            },
            "hook_event_name",
            "SessionEnd",
        ),
        (
            {
                "hook_event_name": "SessionStart",
                "session_id": "thread-42",
                "cwd": "/tmp",
                "source": "startup",
            },
            "session_id",
            "other-thread",
        ),
        (
            {
                "hook_event_name": "SessionStart",
                "session_id": "thread-42",
                "cwd": "/tmp",
                "source": "startup",
            },
            "cwd",
            "/other",
        ),
        (
            {
                "hook_event_name": "SessionStart",
                "session_id": "thread-42",
                "cwd": "/tmp",
                "source": "startup",
            },
            "source",
            "resume",
        ),
        (
            {
                "hook_event_name": "SessionEnd",
                "session_id": "thread-42",
                "cwd": "/tmp",
                "reason": "other",
            },
            "reason",
            "other",
        ),
    ],
)
def test_codex_app_server_rejects_duplicate_authoritative_hook_keys(
    tmp_path: Path, payload: dict[str, str], duplicate_field: str, duplicate_value: str
) -> None:
    adapter, log = _app_server_adapter(tmp_path)
    duplicate_payload = (
        json.dumps(payload)[:-1] + f",{json.dumps(duplicate_field)}:{json.dumps(duplicate_value)}}}"
    )

    result = _invoke_app_server(adapter, tmp_path / "home", log, duplicate_payload)

    assert result.returncode == 64
    assert _events(log) == []


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "{",
        '{"hook_event_name":"SessionStart","session_id":"thread-42","cwd":"/tmp","source":NaN}',
        '{"hook_event_name":"SessionStart","session_id":"thread-42","cwd":"/tmp","source":Infinity}',
        json.dumps([]),
        json.dumps(
            {
                "hook_event_name": "SessionStart",
                "session_id": 42,
                "cwd": "/tmp",
                "source": "startup",
            }
        ),
        json.dumps(
            {
                "hook_event_name": "SessionStart",
                "session_id": "thread-42",
                "cwd": 42,
                "source": "startup",
            }
        ),
        json.dumps(
            {
                "hook_event_name": "SessionStart",
                "session_id": "thread-42",
                "cwd": "relative",
                "source": "startup",
            }
        ),
        json.dumps(
            {
                "hook_event_name": "SessionStart",
                "session_id": "thread-42",
                "cwd": "/definitely-absent-workspace",
                "source": "startup",
            }
        ),
        json.dumps(
            {"hook_event_name": "SessionStart", "session_id": "thread-42", "source": "startup"}
        ),
        json.dumps({"hook_event_name": "SessionEnd", "session_id": "thread-42", "cwd": "/tmp"}),
        json.dumps(
            {
                "hook_event_name": "SessionStart",
                "session_id": "thread\x01",
                "cwd": "/tmp",
                "source": "startup",
            }
        ),
        json.dumps(
            {
                "hook_event_name": "SessionStart",
                "session_id": "thread-42",
                "cwd": "/tmp\x01",
                "source": "startup",
            }
        ),
        json.dumps(
            {
                "hook_event_name": "SessionStart",
                "session_id": "thread-42",
                "cwd": "/tmp",
                "source": "other",
            }
        ),
        json.dumps(
            {
                "hook_event_name": "SessionEnd",
                "session_id": "thread-42",
                "cwd": "/tmp",
                "reason": "startup",
            }
        ),
        json.dumps(
            {
                "hook_event_name": "SessionStart",
                "session_id": "thread-42",
                "cwd": "/tmp",
                "source": "startup",
                "reason": "other",
            }
        ),
        json.dumps(
            {
                "hook_event_name": "SessionEnd",
                "session_id": "thread-42",
                "cwd": "/tmp",
                "reason": "other",
                "source": "startup",
            }
        ),
        json.dumps(
            {
                "hook_event_name": "other",
                "session_id": "thread-42",
                "cwd": "/tmp",
                "source": "startup",
            }
        ),
    ],
)
def test_codex_app_server_rejects_malformed_or_mismatched_hook_envelopes(
    tmp_path: Path, payload: str
) -> None:
    adapter, log = _app_server_adapter(tmp_path)

    result = _invoke_app_server(adapter, tmp_path / "home", log, payload)

    assert result.returncode == 64
    assert _events(log) == []


def test_codex_app_server_propagates_runtime_rejection(tmp_path: Path) -> None:
    adapter, log = _app_server_adapter(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = _invoke_app_server(
        adapter,
        tmp_path / "home",
        log,
        json.dumps(
            {
                "hook_event_name": "SessionStart",
                "session_id": "thread-42",
                "cwd": str(workspace),
                "source": "startup",
            }
        ),
        runtime_exit=23,
    )

    assert result.returncode == 23
    assert _events(log)[0][:3] == ["codex-app-server", "thread-42", "session_start"]

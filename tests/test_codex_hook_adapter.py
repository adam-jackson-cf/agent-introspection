from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ADAPTER_SOURCE = (
    Path(__file__).parents[1] / ".agents/skills/introspection-onboarding/scripts/adapters/codex.py"
)


def _adapter(tmp_path: Path) -> tuple[Path, Path]:
    scripts = tmp_path / "scripts"
    adapters = scripts / "adapters"
    adapters.mkdir(parents=True)
    adapter = adapters / "codex.py"
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


def _events(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def test_codex_notify_emits_start_once_and_workspace_transition(tmp_path: Path) -> None:
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
        "timestamp": "2026-08-03T12:00:00Z",
        "last-assistant-message": "must not be retained",
    }

    assert _invoke(adapter, home, log, first).returncode == 0
    assert _invoke(adapter, home, log, first).returncode == 0
    changed = {**first, "cwd": str(second_workspace), "timestamp": "2026-08-03T12:01:00+00:00"}
    assert _invoke(adapter, home, log, changed).returncode == 0

    assert _events(log) == [
        [
            "codex-cli",
            "thread-42",
            "session_start",
            "2026-08-03T12:00:00+00:00",
            str(first_workspace),
        ],
        [
            "codex-cli",
            "thread-42",
            "workspace_changed",
            "2026-08-03T12:01:00+00:00",
            str(second_workspace),
        ],
    ]
    state_files = list((home / ".local/state/agent-introspection/codex-hook").glob("*.json"))
    assert len(state_files) == 1
    assert json.loads(state_files[0].read_text(encoding="utf-8")) == {
        "session_id": "thread-42",
        "workspace": str(second_workspace),
    }


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
    "duplicate_field",
    ["type", "thread-id", "cwd", "timestamp"],
)
def test_codex_notify_rejects_duplicate_authoritative_fields_without_state_mutation(
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
            "session_start",
            "2026-08-03T12:00:00+00:00",
            str(original_workspace),
        ]
    ]
    state_files = list((home / ".local/state/agent-introspection/codex-hook").glob("*.json"))
    assert len(state_files) == 1
    assert json.loads(state_files[0].read_text(encoding="utf-8")) == {
        "session_id": "thread-42",
        "workspace": str(original_workspace),
    }

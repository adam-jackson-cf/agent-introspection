from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

RUNTIME = (
    Path(__file__).resolve().parents[1]
    / ".agents/skills/introspection-onboarding/scripts/session-context-runtime.sh"
)


def make_repository(tmp_path: Path, name: str = "project") -> Path:
    workspace = tmp_path / name
    workspace.mkdir()
    subprocess.run(["git", "init", "--quiet", str(workspace)], check=True)
    return workspace


def run_runtime(home: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ | {"HOME": str(home)}
    return subprocess.run(
        [str(RUNTIME), *arguments],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def inbox(home: Path) -> Path:
    return home / ".local/share/agent-introspection/session-context-inbox"


@pytest.mark.parametrize("symlink_workspace", [False, True])
@pytest.mark.parametrize("occurred_at", ["2026-08-03T12:34:56Z", "2026-08-03T12:34:56.123+05:30"])
def test_runtime_writes_compact_canonical_json_for_valid_rfc3339_events(
    tmp_path: Path, occurred_at: str, symlink_workspace: bool
) -> None:
    workspace = make_repository(tmp_path)
    if symlink_workspace:
        alias = tmp_path / "workspace"
        alias.symlink_to(workspace, target_is_directory=True)
        workspace = alias
    home = tmp_path / "home"
    completed = run_runtime(
        home, "codex-cli", "session-123", "session_start", occurred_at, str(workspace)
    )

    assert completed.returncode == 0, completed.stderr
    records = list(inbox(home).glob("*.json"))
    assert len(records) == 1
    raw_record = records[0].read_text()
    record = json.loads(raw_record)
    root = str(workspace.resolve())
    expected_project_id = hashlib.sha256(f"git\0{root}".encode()).hexdigest()
    expected_event_id = hashlib.sha256(
        f"codex-cli\0session-123\0session_start\0{occurred_at}\0{root}".encode()
    ).hexdigest()
    assert record == {
        "event_id": expected_event_id,
        "producer": "codex-cli",
        "session_id": "session-123",
        "event_type": "session_start",
        "occurred_at": occurred_at,
        "agent": {
            "project": {
                "id": expected_project_id,
                "name": Path(root).name,
                "root": root,
                "kind": "git",
            }
        },
    }
    assert raw_record == json.dumps(record, separators=(",", ":")) + "\n"


@pytest.mark.parametrize(
    "occurred_at",
    [
        "2026-02-30T12:34:56Z",
        "2026-13-01T12:34:56+00:00",
        "2026-08-03T24:00:00Z",
        "2026-08-03T12:34:56",
        "2026-08-03 12:34:56Z",
    ],
)
def test_runtime_rejects_invalid_rfc3339_calendar_values_before_inbox_commit(
    tmp_path: Path, occurred_at: str
) -> None:
    workspace = make_repository(tmp_path)
    home = tmp_path / "home"

    completed = run_runtime(home, "omp", "session-123", "session_end", occurred_at, str(workspace))

    assert completed.returncode != 0
    assert not inbox(home).exists()


ASCII_CONTROLS = [chr(value) for value in (*range(1, 32), 127)]


@pytest.mark.parametrize("control", ASCII_CONTROLS, ids=lambda value: f"0x{ord(value):02x}")
@pytest.mark.parametrize("field", ["session_id", "occurred_at", "workspace", "project_root"])
def test_runtime_rejects_every_representable_ascii_control_before_inbox_commit(
    tmp_path: Path, field: str, control: str
) -> None:
    workspace = make_repository(
        tmp_path,
        f"project{control}" if field in {"workspace", "project_root"} else "project",
    )
    if field == "project_root":
        alias = tmp_path / "workspace"
        alias.symlink_to(workspace, target_is_directory=True)
        workspace = alias
    home = tmp_path / "home"
    arguments = {
        "session_id": "session-123",
        "occurred_at": "2026-08-03T12:34:56Z",
        "workspace": str(workspace),
    }
    if field not in {"workspace", "project_root"}:
        arguments[field] = f"{arguments[field]}{control}"

    completed = run_runtime(
        home,
        "claude-code",
        arguments["session_id"],
        "session_start",
        arguments["occurred_at"],
        arguments["workspace"],
    )

    assert completed.returncode != 0
    assert not inbox(home).exists()

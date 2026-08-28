from __future__ import annotations

import importlib.util
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

PROXY_SOURCE = (
    Path(__file__).parents[1]
    / ".agents/skills/introspection-onboarding/scripts/adapters/codex-app-server/proxy.py"
)


def _load_proxy() -> ModuleType:
    spec = importlib.util.spec_from_file_location("codex_app_server_proxy", PROXY_SOURCE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


proxy = _load_proxy()


def _request(request_id: int, method: str, params: dict[str, object]) -> bytes:
    return json.dumps({"id": request_id, "method": method, "params": params}).encode() + b"\n"


def _thread_response(
    request_id: int,
    thread_id: str,
    workspace: Path,
    **additional: object,
) -> bytes:
    result = {
        "cwd": str(workspace),
        "thread": {"id": thread_id, "cwd": str(workspace)},
        **additional,
    }
    return json.dumps({"id": request_id, "result": result}).encode() + b"\n"


def _success_response(request_id: int) -> bytes:
    return json.dumps({"id": request_id, "result": {}}).encode() + b"\n"


def _error_response(request_id: int) -> bytes:
    return json.dumps({"id": request_id, "error": {"message": "rejected"}}).encode() + b"\n"


def _runtime_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any, Path]:
    scripts = tmp_path / "scripts"
    adapter_dir = scripts / "adapters" / "codex-app-server"
    adapter_dir.mkdir(parents=True)
    adapter = adapter_dir / "adapter.sh"
    adapter.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "import sys\n"
        "with open(os.environ['EVENT_LOG'], 'a', encoding='utf-8') as output:\n"
        "    json.dump(sys.argv[1:], output)\n"
        "    output.write('\\n')\n",
        encoding="utf-8",
    )
    adapter.chmod(0o755)
    event_log = tmp_path / "events.jsonl"
    monkeypatch.setenv("EVENT_LOG", str(event_log))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    worker = proxy.LifecycleWorker(adapter_dir)
    observer = proxy.ProtocolObserver(worker)
    worker.start()
    return worker, observer, event_log


def _events(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_protocol_responses_drive_one_canonical_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    worker, observer, event_log = _runtime_layout(tmp_path, monkeypatch)

    observer.client_line(
        _request(
            1,
            "thread/start",
            {
                "cwd": str(first),
                "prompt": "sensitive prompt must only be forwarded",
                "history": [{"response": "sensitive response must only be forwarded"}],
            },
        )
    )
    observer.server_line(
        _thread_response(
            1,
            "thread-42",
            first,
            title="sensitive title must only be forwarded",
        )
    )
    observer.client_line(_request(2, "thread/resume", {"threadId": "thread-42"}))
    observer.server_line(_thread_response(2, "thread-42", first))
    observer.client_line(_request(3, "thread/resume", {"threadId": "thread-42"}))
    observer.server_line(_thread_response(3, "thread-42", second))
    observer.client_line(_request(4, "thread/delete", {"threadId": "thread-42"}))
    observer.server_line(_success_response(4))
    observer.server_line(b'{"method":"thread/deleted","params":{"threadId":"thread-42"}}\n')
    worker.close()

    events = _events(event_log)
    assert [event[:3] for event in events] == [
        ["thread-42", str(first), "session_start"],
        ["thread-42", str(second), "workspace_changed"],
        ["thread-42", str(second), "session_end"],
    ]
    retained = (
        b"".join(path.read_bytes() for path in (tmp_path / "state").rglob("*") if path.is_file())
        + event_log.read_bytes()
    )
    assert b"sensitive" not in retained
    assert list((tmp_path / "state").rglob("*.json")) == []


def test_server_notifications_drive_native_workspace_and_end_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    worker, observer, event_log = _runtime_layout(tmp_path, monkeypatch)

    observer.server_line(
        json.dumps(
            {
                "method": "thread/started",
                "params": {"thread": {"id": "thread-42", "cwd": str(first)}},
            }
        ).encode()
        + b"\n"
    )
    observer.server_line(
        json.dumps(
            {
                "method": "thread/settings/updated",
                "params": {
                    "threadId": "thread-42",
                    "threadSettings": {"cwd": str(second)},
                },
            }
        ).encode()
        + b"\n"
    )
    observer.client_line(_request(1, "thread/delete", {"threadId": "thread-42"}))
    observer.server_line(b'{"method":"thread/deleted","params":{"threadId":"thread-42"}}\n')
    observer.server_line(_success_response(1))
    worker.close()

    assert [event[:3] for event in _events(event_log)] == [
        ["thread-42", str(first), "session_start"],
        ["thread-42", str(second), "workspace_changed"],
        ["thread-42", str(second), "session_end"],
    ]


def test_unknown_unsuccessful_ambiguous_and_malformed_lifecycles_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    worker, observer, event_log = _runtime_layout(tmp_path, monkeypatch)

    observer.client_line(_request(1, "thread/resume", {"threadId": "unknown"}))
    observer.server_line(_thread_response(1, "unknown", workspace))
    observer.client_line(_request(2, "thread/delete", {"threadId": "unknown"}))
    observer.server_line(_success_response(2))
    observer.client_line(_request(3, "thread/start", {"cwd": str(workspace)}))
    observer.server_line(_error_response(3))

    observer.client_line(_request(4, "thread/start", {"cwd": str(workspace)}))
    observer.client_line(_request(4, "thread/resume", {"threadId": "other"}))
    observer.server_line(_thread_response(4, "thread-42", workspace))
    observer.client_line(b'{"id":5,"method":"thread/start","method":"thread/resume","params":{}}\n')
    observer.server_line(_thread_response(5, "thread-42", workspace))
    worker.close()

    assert _events(event_log) == []
    assert list((tmp_path / "state").rglob("*.json")) == []


def test_pending_and_managed_state_are_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(proxy, "_MAX_PENDING", 2)
    monkeypatch.setattr(proxy, "_MAX_STATE", 1)
    worker, observer, event_log = _runtime_layout(tmp_path, monkeypatch)

    observer.client_line(_request(1, "thread/start", {"cwd": str(workspace)}))
    observer.client_line(_request(2, "thread/start", {"cwd": str(workspace)}))
    observer.client_line(_request(3, "thread/start", {"cwd": str(workspace)}))
    observer.server_line(_thread_response(1, "evicted", workspace))
    observer.server_line(_thread_response(2, "accepted", workspace))
    observer.server_line(_thread_response(3, "capacity-rejected", workspace))
    worker.close()

    assert [event[:3] for event in _events(event_log)] == [
        ["accepted", str(workspace), "session_start"]
    ]
    assert len(list((tmp_path / "state").rglob("*.json"))) == 1


def _proxy_layout(tmp_path: Path) -> tuple[Path, Path]:
    scripts = tmp_path / "scripts"
    adapter_dir = scripts / "adapters" / "codex-app-server"
    adapter_dir.mkdir(parents=True)
    installed_proxy = adapter_dir / "proxy.py"
    shutil.copy2(PROXY_SOURCE, installed_proxy)
    installed_proxy.chmod(installed_proxy.stat().st_mode | stat.S_IXUSR)
    adapter = adapter_dir / "adapter.sh"
    adapter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    adapter.chmod(0o755)
    real_cli = tmp_path / "real-codex"
    real_cli.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "import signal\n"
        "import sys\n"
        "import time\n"
        "if sys.argv[1:2] != ['app-server']:\n"
        "    output = {'argv': sys.argv[1:], 'override': 'CODEX_CLI_PATH' in os.environ}\n"
        "    print(json.dumps(output))\n"
        "    raise SystemExit(int(os.environ.get('FAKE_EXIT', '0')))\n"
        "if os.environ.get('FAKE_WAIT') == '1':\n"
        "    time.sleep(0.1)\n"
        "    print('ready', file=sys.stderr, flush=True)\n"
        "    signal.pause()\n"
        "for line in sys.stdin.buffer:\n"
        "    sys.stdout.buffer.write(line)\n"
        "    sys.stdout.buffer.flush()\n"
        "raise SystemExit(int(os.environ.get('FAKE_EXIT', '0')))\n",
        encoding="utf-8",
    )
    real_cli.chmod(0o755)
    (adapter_dir / "codex-app-server-real-cli").write_text(f"{real_cli}\n", encoding="utf-8")
    return installed_proxy, real_cli


def test_process_proxy_forwards_protocol_bytes_and_child_status_exactly(tmp_path: Path) -> None:
    installed_proxy, _ = _proxy_layout(tmp_path)
    protocol = b'{"method":"other","params":{"prompt":"forward exactly \\u2603"}}\n'
    environment = {
        **os.environ,
        "CODEX_CLI_PATH": str(installed_proxy),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "FAKE_EXIT": "37",
    }

    result = subprocess.run(
        [str(installed_proxy), "app-server"],
        input=protocol,
        capture_output=True,
        check=False,
        env=environment,
    )

    assert result.returncode == 37
    assert result.stdout == protocol


def test_non_app_server_invocation_execs_real_cli_without_override(tmp_path: Path) -> None:
    installed_proxy, _ = _proxy_layout(tmp_path)

    result = subprocess.run(
        [str(installed_proxy), "--version"],
        capture_output=True,
        check=False,
        env={**os.environ, "CODEX_CLI_PATH": str(installed_proxy)},
        text=True,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {"argv": ["--version"], "override": False}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal propagation contract")
def test_process_proxy_exits_with_the_child_signal(tmp_path: Path) -> None:
    installed_proxy, _ = _proxy_layout(tmp_path)
    process = subprocess.Popen(
        [str(installed_proxy), "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            **os.environ,
            "CODEX_CLI_PATH": str(installed_proxy),
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "FAKE_WAIT": "1",
        },
    )
    assert process.stderr is not None
    assert process.stderr.readline() == b"ready\n"

    process.send_signal(signal.SIGTERM)

    assert process.wait(timeout=5) == -signal.SIGTERM

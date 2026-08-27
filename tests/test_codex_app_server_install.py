from __future__ import annotations

import importlib.util
import json
import plistlib
import stat
import sys
from pathlib import Path
from types import ModuleType

import pytest

INSTALLER_SOURCE = (
    Path(__file__).parents[1]
    / ".agents/skills/introspection-onboarding/scripts/install-codex-app-server.py"
)
SCRIPT_SOURCE = INSTALLER_SOURCE.parent


def _load_installer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("install_codex_app_server", INSTALLER_SOURCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


installer = _load_installer()


def _executable(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_installer_atomically_manages_proxy_runtime_adapter_and_launch_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    real_codex = _executable(tmp_path / "real-codex", "#!/bin/sh\nexit 0\n")
    state = tmp_path / "launchctl-state"
    calls = tmp_path / "launchctl-calls.jsonl"
    launchctl = _executable(
        tmp_path / "launchctl",
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "import plistlib\n"
        "import sys\n"
        "from pathlib import Path\n"
        "state = Path(os.environ['FAKE_LAUNCHCTL_STATE'])\n"
        "calls = Path(os.environ['FAKE_LAUNCHCTL_CALLS'])\n"
        "with calls.open('a', encoding='utf-8') as output:\n"
        "    json.dump(sys.argv[1:], output)\n"
        "    output.write('\\n')\n"
        "command = sys.argv[1]\n"
        "if command == 'print':\n"
        "    raise SystemExit(0 if state.exists() else 1)\n"
        "if command == 'bootout':\n"
        "    state.unlink()\n"
        "elif command == 'bootstrap':\n"
        "    payload = plistlib.loads(Path(sys.argv[3]).read_bytes())\n"
        "    state.write_text(payload['ProgramArguments'][-1], encoding='utf-8')\n"
        "elif command == 'getenv':\n"
        "    print(state.read_text(encoding='utf-8'))\n",
    )
    monkeypatch.setenv("FAKE_LAUNCHCTL_STATE", str(state))
    monkeypatch.setenv("FAKE_LAUNCHCTL_CALLS", str(calls))

    proxy, launch_agent = installer.install(
        home=home,
        real_codex=real_codex,
        launchctl=launchctl,
    )
    installer.install(home=home, real_codex=real_codex, launchctl=launchctl)

    managed = home / ".local/lib/agent-introspection/session-context-runtime-v1"
    assert proxy.read_bytes() == (SCRIPT_SOURCE / "codex-app-server-proxy.py").read_bytes()
    assert (managed / "session-context-runtime.sh").read_bytes() == (
        SCRIPT_SOURCE / "session-context-runtime.sh"
    ).read_bytes()
    assert (managed / "adapters/codex-app-server.sh").read_bytes() == (
        SCRIPT_SOURCE / "adapters/codex-app-server.sh"
    ).read_bytes()
    assert stat.S_IMODE(proxy.stat().st_mode) == 0o755
    assert stat.S_IMODE((managed / "codex-app-server-real-cli").stat().st_mode) == 0o444
    assert (managed / "codex-app-server-real-cli").read_text(encoding="utf-8") == (
        f"{real_codex.resolve()}\n"
    )

    payload = plistlib.loads(launch_agent.read_bytes())
    assert payload == {
        "Label": "com.adamjackson.agent-introspection-codex-app-server",
        "ProcessType": "Background",
        "ProgramArguments": [
            "/bin/launchctl",
            "setenv",
            "CODEX_CLI_PATH",
            str(proxy),
        ],
        "RunAtLoad": True,
    }
    observed_calls = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
    assert [call[0] for call in observed_calls] == [
        "print",
        "bootstrap",
        "getenv",
        "print",
        "bootout",
        "bootstrap",
        "getenv",
    ]
    assert state.read_text(encoding="utf-8") == str(proxy)


def test_installer_rejects_missing_real_codex_without_partial_install(tmp_path: Path) -> None:
    home = tmp_path / "home"

    with pytest.raises(installer.InstallError, match="bundled Codex executable"):
        installer.install(
            home=home,
            real_codex=tmp_path / "missing",
            activate=False,
        )

    assert not (home / ".local/lib/agent-introspection").exists()

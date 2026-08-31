from __future__ import annotations

import hashlib
import importlib.util
import json
import plistlib
import shlex
import stat
import sys
from pathlib import Path
from types import ModuleType

import pytest

INSTALLER_SOURCE = (
    Path(__file__).parents[1]
    / ".agents/skills/introspection-onboarding/scripts/adapters/codex-app-server/install.py"
)
ADAPTER_SOURCE = INSTALLER_SOURCE.parent
SCRIPTS_SOURCE = ADAPTER_SOURCE.parent.parent


def _load_installer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("install_codex_app_server", INSTALLER_SOURCE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


installer = _load_installer()


@pytest.fixture(autouse=True)
def _isolate_codex_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CODEX_HOME", raising=False)


def _executable(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _launchctl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str = "",
    *,
    service: bool = False,
    failing: str = "",
) -> tuple[Path, Path]:
    state = tmp_path / "launchctl-state.json"
    state.write_text(
        json.dumps({"value": value, "service": service, "failing": failing, "calls": []}),
        encoding="utf-8",
    )
    launchctl = _executable(
        tmp_path / "launchctl",
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n"
        "state = Path(os.environ['FAKE_LAUNCHCTL_STATE'])\n"
        "payload = json.loads(state.read_text(encoding='utf-8'))\n"
        "payload['calls'].append(sys.argv[1:])\n"
        "if sys.argv[1] == payload['failing']:\n"
        "    status = 2\n"
        "elif sys.argv[1] == 'print':\n"
        "    status = 0 if payload['service'] else 1\n"
        "elif sys.argv[1] == 'getenv' and payload['value']:\n"
        "    print(payload['value'])\n"
        "    status = 0\n"
        "elif sys.argv[1] == 'getenv':\n"
        "    status = 1\n"
        "elif sys.argv[1] == 'unsetenv':\n"
        "    payload['value'] = ''\n"
        "    status = 0\n"
        "else:\n"
        "    status = 0\n"
        "state.write_text(json.dumps(payload), encoding='utf-8')\n"
        "raise SystemExit(status)\n",
    )
    monkeypatch.setenv("FAKE_LAUNCHCTL_STATE", str(state))
    return launchctl, state


def _owned_hooks(adapter: Path) -> dict[str, list[dict[str, object]]]:
    command = f"/usr/bin/python3 {shlex.quote(str(adapter))}"
    return {
        "SessionStart": [
            {
                "matcher": "^(startup|resume|clear|compact)$",
                "hooks": [{"type": "command", "command": command}],
            }
        ],
        "SessionEnd": [
            {
                "matcher": "^other$",
                "hooks": [{"type": "command", "command": command}],
            }
        ],
    }


def _legacy_plist(home: Path, proxy: Path) -> Path:
    path = home / "Library/LaunchAgents/com.adamjackson.agent-introspection-codex-app-server.plist"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        plistlib.dumps(
            {
                "Label": "com.adamjackson.agent-introspection-codex-app-server",
                "ProcessType": "Background",
                "ProgramArguments": ["/bin/launchctl", "setenv", "CODEX_CLI_PATH", str(proxy)],
                "RunAtLoad": True,
            }
        )
    )
    return path


def _legacy_thread_state(home: Path, thread_id: str = "thread") -> Path:
    state = home / ".local/state/agent-introspection/codex-app-server-threads"
    workspace = home / "workspace"
    workspace.mkdir(parents=True)
    state.mkdir(parents=True)
    (state / ".state.lock").touch()
    digest = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()
    (state / f"{digest}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "thread_id": thread_id,
                "workspace": str(workspace),
            }
        ),
        encoding="utf-8",
    )
    return state


def test_installer_atomically_installs_runtime_adapter_and_exact_owned_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    hooks_path = home / ".codex/hooks.json"
    hooks_path.parent.mkdir(parents=True)
    hooks_path.write_text(
        json.dumps(
            {
                "version": 7,
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {"type": "command", "command": "/usr/local/bin/user-hook --safe"}
                            ]
                        }
                    ],
                    "SessionStart": [
                        {
                            "matcher": "^manual$",
                            "hooks": [{"type": "command", "command": "/usr/local/bin/user-start"}],
                        }
                    ],
                },
                "user_setting": {"keep": True},
            }
        ),
        encoding="utf-8",
    )
    launchctl, state = _launchctl(tmp_path, monkeypatch, "/unrelated/codex")

    adapter, installed_hooks = installer.install(home=home, launchctl=launchctl)
    first_install = installed_hooks.read_bytes()
    installer.install(home=home, launchctl=launchctl)

    managed = home / ".local/lib/agent-introspection/session-context-runtime-v1"
    assert adapter == managed / "adapters/codex-app-server/adapter.py"
    assert adapter.read_bytes() == (ADAPTER_SOURCE / "adapter.py").read_bytes()
    assert (managed / "session-context-runtime.sh").read_bytes() == (
        SCRIPTS_SOURCE / "session-context-runtime.sh"
    ).read_bytes()
    assert stat.S_IMODE(adapter.stat().st_mode) == 0o755
    assert stat.S_IMODE((managed / "session-context-runtime.sh").stat().st_mode) == 0o755
    assert installed_hooks.read_bytes() == first_install

    payload = json.loads(installed_hooks.read_text(encoding="utf-8"))
    assert payload["version"] == 7
    assert payload["user_setting"] == {"keep": True}
    assert payload["hooks"]["PreToolUse"] == [
        {"hooks": [{"type": "command", "command": "/usr/local/bin/user-hook --safe"}]}
    ]
    assert payload["hooks"]["SessionStart"] == [
        {
            "matcher": "^manual$",
            "hooks": [{"type": "command", "command": "/usr/local/bin/user-start"}],
        },
        *_owned_hooks(adapter)["SessionStart"],
    ]
    assert payload["hooks"]["SessionEnd"] == _owned_hooks(adapter)["SessionEnd"]
    assert json.loads(state.read_text(encoding="utf-8"))["value"] == "/unrelated/codex"
    assert all(
        call[0] != "unsetenv" for call in json.loads(state.read_text(encoding="utf-8"))["calls"]
    )


def test_installer_uses_default_codex_root_when_override_is_unset(tmp_path: Path) -> None:
    home = tmp_path / "home"

    _, hooks_path = installer.install(home=home, activate=False)

    assert hooks_path == home / ".codex/hooks.json"
    assert hooks_path.is_file()


def test_installer_merges_hooks_in_absolute_codex_home_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    active_root = tmp_path / "active-codex"
    active_root.mkdir()
    active_hooks = active_root / "hooks.json"
    original = {
        "hooks": {
            "PreToolUse": [
                {"hooks": [{"type": "command", "command": "/usr/local/bin/user-hook --safe"}]}
            ]
        }
    }
    active_hooks.write_text(json.dumps(original), encoding="utf-8")
    default_hooks = home / ".codex/hooks.json"
    default_hooks.parent.mkdir(parents=True)
    default_hooks.write_text('{"hooks": {"Notification": []}}', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(active_root))

    adapter, hooks_path = installer.install(home=home, activate=False)

    assert hooks_path == active_hooks
    payload = json.loads(active_hooks.read_text(encoding="utf-8"))
    assert payload["hooks"]["PreToolUse"] == original["hooks"]["PreToolUse"]
    assert payload["hooks"]["SessionStart"] == _owned_hooks(adapter)["SessionStart"]
    assert payload["hooks"]["SessionEnd"] == _owned_hooks(adapter)["SessionEnd"]
    assert default_hooks.read_text(encoding="utf-8") == '{"hooks": {"Notification": []}}'


@pytest.mark.parametrize("override", ["", "relative/.codex", "missing-parent/.codex"])
def test_invalid_codex_home_override_fails_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, override: str
) -> None:
    home = tmp_path / "home"
    default_hooks = home / ".codex/hooks.json"
    default_hooks.parent.mkdir(parents=True)
    default_hooks.write_text('{"hooks": {}}', encoding="utf-8")
    value = str(tmp_path / override) if override == "missing-parent/.codex" else override
    monkeypatch.setenv("CODEX_HOME", value)

    with pytest.raises(installer.InstallError):
        installer.install(home=home, activate=False)

    assert default_hooks.read_text(encoding="utf-8") == '{"hooks": {}}'
    assert not (home / ".local/lib/agent-introspection").exists()


def test_non_directory_codex_home_override_fails_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    override = tmp_path / "not-a-directory"
    override.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(override))

    with pytest.raises(installer.InstallError):
        installer.install(home=home, activate=False)

    assert not (home / ".local/lib/agent-introspection").exists()


@pytest.mark.parametrize(
    "invalid",
    [
        "[]",
        "{",
        '{"hooks": []}',
        '{"hooks": {"SessionStart": {}}}',
        '{"hooks": {}, "hooks": {}}',
    ],
)
def test_invalid_existing_hooks_fail_closed_without_partial_install(
    tmp_path: Path, invalid: str
) -> None:
    home = tmp_path / "home"
    hooks_path = home / ".codex/hooks.json"
    hooks_path.parent.mkdir(parents=True)
    hooks_path.write_text(invalid, encoding="utf-8")

    with pytest.raises(installer.InstallError):
        installer.install(home=home, activate=False)

    assert hooks_path.read_text(encoding="utf-8") == invalid
    assert not (home / ".local/lib/agent-introspection").exists()


def test_installer_removes_only_owned_legacy_contracts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    managed = home / ".local/lib/agent-introspection/session-context-runtime-v1"
    nested = managed / "adapters/codex-app-server"
    root_proxy = managed / "codex-app-server-proxy.py"
    nested_proxy = nested / "proxy.py"
    for path in (
        root_proxy,
        managed / "codex-app-server-real-cli",
        managed / "adapters/codex-app-server.sh",
        nested_proxy,
        nested / "adapter.sh",
        nested / "codex-app-server-real-cli",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("legacy", encoding="utf-8")
    _legacy_plist(home, root_proxy)
    thread_state = _legacy_thread_state(home)
    unrelated = managed / "adapters/codex-app-server/user-hook"
    unrelated.write_text("keep", encoding="utf-8")
    launchctl, state = _launchctl(tmp_path, monkeypatch, str(root_proxy), service=True)

    adapter, _ = installer.install(home=home, launchctl=launchctl)

    assert adapter.exists()
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert not root_proxy.exists()
    assert not nested_proxy.exists()
    assert not (managed / "codex-app-server-real-cli").exists()
    assert not (managed / "adapters/codex-app-server.sh").exists()
    assert not (nested / "adapter.sh").exists()
    assert not (nested / "codex-app-server-real-cli").exists()
    assert not thread_state.exists()
    assert not (
        home / "Library/LaunchAgents/com.adamjackson.agent-introspection-codex-app-server.plist"
    ).exists()
    launch_state = json.loads(state.read_text(encoding="utf-8"))
    assert launch_state["value"] == ""
    assert [call[0] for call in launch_state["calls"]] == ["print", "bootout", "getenv", "unsetenv"]


def test_activate_requires_usable_launchctl_before_mutation(tmp_path: Path) -> None:
    home = tmp_path / "home"

    with pytest.raises(installer.InstallError):
        installer.install(home=home, launchctl=tmp_path / "missing-launchctl")

    assert not (home / ".local/lib/agent-introspection").exists()
    assert not (home / ".codex/hooks.json").exists()


@pytest.mark.parametrize("failing", ["bootout", "unsetenv"])
def test_legacy_teardown_failures_are_not_reported_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing: str
) -> None:
    home = tmp_path / "home"
    managed = home / ".local/lib/agent-introspection/session-context-runtime-v1"
    root_proxy = managed / "codex-app-server-proxy.py"
    root_proxy.parent.mkdir(parents=True)
    root_proxy.write_text("legacy", encoding="utf-8")
    _legacy_plist(home, root_proxy)
    hooks_path = home / ".codex/hooks.json"
    hooks_path.parent.mkdir(parents=True)
    hooks_path.write_text('{"hooks": {}}', encoding="utf-8")
    launchctl, _ = _launchctl(
        tmp_path,
        monkeypatch,
        str(root_proxy),
        service=True,
        failing=failing,
    )

    with pytest.raises(installer.InstallError):
        installer.install(home=home, launchctl=launchctl)
    assert hooks_path.read_text(encoding="utf-8") == '{"hooks": {}}'


def test_installer_shell_quotes_adapter_for_spaced_metacharacter_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home with spaces;$(inert)"
    launchctl, _ = _launchctl(tmp_path, monkeypatch)

    adapter, hooks_path = installer.install(home=home, launchctl=launchctl)

    payload = json.loads(hooks_path.read_text(encoding="utf-8"))
    command = payload["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert command == f"/usr/bin/python3 {shlex.quote(str(adapter))}"


@pytest.mark.parametrize(
    ("event", "command_template"),
    [
        ("SessionStart", "{adapter}"),
        ("SessionStart", "python3 {adapter} --unexpected"),
        ("SessionStart", "python3 -u {adapter} --unexpected"),
        ("SessionStart", "/usr/bin/python3 {adapter} --unexpected"),
        ("SessionStart", "/usr/bin/python3 -B {adapter} --unexpected"),
        ("SessionStart", "/usr/bin/env python3 {adapter} --unexpected"),
        ("SessionStart", "/usr/bin/env python3 -u {adapter} --unexpected"),
        (
            "SessionStart",
            "/usr/bin/env -i PYTHONUNBUFFERED=1 python3 -B {adapter} --unexpected",
        ),
        ("SessionStart", "PYTHONUNBUFFERED=1 {adapter}"),
        ("SessionStart", "command {adapter}"),
        ("SessionStart", "exec {adapter}"),
        ("SessionStart", "nohup {adapter}"),
        ("SessionStart", "PYTHONUNBUFFERED=1 command exec nohup python3 -u {adapter}"),
        (
            "SessionStart",
            "PYTHONUNBUFFERED=1 command exec nohup "
            "/usr/bin/env -i ADAPTER_MODE=hook python3 -B {adapter}",
        ),
        ("SessionStart", "python3 {adapter} &&"),
        ("PreToolUse", "{adapter}"),
    ],
)
def test_alternate_managed_hook_commands_fail_closed_before_mutation(
    tmp_path: Path, event: str, command_template: str
) -> None:
    home = tmp_path / "home"
    adapter = home / (
        ".local/lib/agent-introspection/session-context-runtime-v1/"
        "adapters/codex-app-server/adapter.py"
    )
    hooks_path = home / ".codex/hooks.json"
    hooks_path.parent.mkdir(parents=True)
    original = {
        "hooks": {
            event: [
                {
                    "matcher": "^manual$",
                    "hooks": [
                        {
                            "type": "command",
                            "command": command_template.format(adapter=adapter),
                        }
                    ],
                }
            ]
        }
    }
    hooks_path.write_text(json.dumps(original), encoding="utf-8")

    with pytest.raises(installer.InstallError):
        installer.install(home=home, activate=False)

    assert json.loads(hooks_path.read_text(encoding="utf-8")) == original
    assert not adapter.exists()


@pytest.mark.parametrize(
    "command_template",
    [
        "echo PYTHONUNBUFFERED=1 {adapter}",
        "echo PYTHONUNBUFFERED=1 python3 {adapter}",
        "echo PYTHONUNBUFFERED=1 /usr/bin/env -i ADAPTER_MODE=hook python3 {adapter}",
    ],
)
def test_unrelated_hook_echoing_adapter_path_is_preserved(
    tmp_path: Path, command_template: str
) -> None:
    home = tmp_path / "home"
    adapter = home / (
        ".local/lib/agent-introspection/session-context-runtime-v1/"
        "adapters/codex-app-server/adapter.py"
    )
    hooks_path = home / ".codex/hooks.json"
    original = {
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "^manual$",
                    "hooks": [
                        {
                            "type": "command",
                            "command": command_template.format(adapter=adapter),
                        }
                    ],
                }
            ]
        }
    }
    hooks_path.parent.mkdir(parents=True)
    hooks_path.write_text(json.dumps(original), encoding="utf-8")

    installer.install(home=home, activate=False)

    payload = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert payload["hooks"]["SessionStart"][0] == original["hooks"]["SessionStart"][0]


def test_unowned_legacy_plist_is_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    managed = home / ".local/lib/agent-introspection/session-context-runtime-v1"
    root_proxy = managed / "codex-app-server-proxy.py"
    root_proxy.parent.mkdir(parents=True)
    root_proxy.write_text("legacy", encoding="utf-8")
    protected = (
        home / "Library/LaunchAgents/com.adamjackson.agent-introspection-codex-app-server.plist"
    )
    protected.parent.mkdir(parents=True)
    protected.write_bytes(plistlib.dumps({"Label": "reused"}))
    launchctl, _ = _launchctl(tmp_path, monkeypatch)

    with pytest.raises(installer.InstallError):
        installer.install(home=home, launchctl=launchctl)

    assert protected.exists()
    assert not (home / ".codex/hooks.json").exists()


@pytest.mark.parametrize("kind", ["malformed_schema", "invalid_hash", "unrelated"])
def test_unowned_legacy_thread_state_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    home = tmp_path / "home"
    state = _legacy_thread_state(home)
    thread_id = "thread"
    owned = state / f"{hashlib.sha256(thread_id.encode('utf-8')).hexdigest()}.json"
    if kind == "malformed_schema":
        protected = owned
        protected.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "thread_id": thread_id,
                    "workspace": str(home / "workspace"),
                }
            ),
            encoding="utf-8",
        )
    elif kind == "invalid_hash":
        protected = state / f"{'0' * 64}.json"
        owned.replace(protected)
    else:
        protected = state / "unrelated.txt"
        protected.write_text("keep", encoding="utf-8")
    original = protected.read_bytes()
    launchctl, _ = _launchctl(tmp_path, monkeypatch)

    with pytest.raises(installer.InstallError):
        installer.install(home=home, launchctl=launchctl)

    assert protected.read_bytes() == original
    assert state.exists()
    assert not (home / ".codex/hooks.json").exists()

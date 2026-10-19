from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path

import pytest

from agent_introspection import config, doctor_harness, harness_config, hook_install
from agent_introspection.hook_install import HookInstallError, HookOptions
from tests.conftest import FakeOmp

ORCA = {
    "hooks": [
        {"type": "command", "command": "/bin/sh '/Users/me/.orca/agent-hooks/claude-hook.sh'"}
    ]
}


def test_the_runtime_is_copied_from_the_package_with_modes_and_backups(tmp_path: Path) -> None:
    runtime = tmp_path / doctor_harness.MANAGED_RUNTIME
    shim = runtime / doctor_harness.SHIM
    shim.parent.mkdir(parents=True)
    shim.write_text("#!/bin/sh\n# user-edited\n")

    planned = hook_install.install_runtime(HookOptions(home=tmp_path, dry_run=True), "omp")
    assert [item["action"] for item in planned] == ["change", "add", "add", "unchanged"]
    assert shim.read_text() == "#!/bin/sh\n# user-edited\n"

    installed = hook_install.install_runtime(HookOptions(home=tmp_path), "omp")

    assert shim.read_text() == hook_install.packaged(doctor_harness.SHIM)
    assert shim.stat().st_mode & 0o777 == 0o755
    assert (runtime / "omp/activity.ts").stat().st_mode & 0o777 == 0o644
    backups = list(runtime.glob(f"{doctor_harness.SHIM}.bak-*"))
    assert [path.read_text() for path in backups] == ["#!/bin/sh\n# user-edited\n"]
    assert installed[0]["backup"] == str(backups[0])
    again = hook_install.install_runtime(HookOptions(home=tmp_path), "omp")
    assert {item["action"] for item in again} == {"unchanged"}


def test_claude_code_gets_the_shim_without_omps_plugin_files(tmp_path: Path) -> None:
    installed = hook_install.install_runtime(HookOptions(home=tmp_path), "claude-code")

    runtime = tmp_path / doctor_harness.MANAGED_RUNTIME
    assert [Path(item["path"]).name for item in installed[:-1]] == [doctor_harness.SHIM]
    assert not (runtime / "omp").exists()


def test_claude_hooks_are_idempotent_keep_other_tools_and_remove_cleanly(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    original = {
        "env": {"A": "1"},
        "hooks": {"PreToolUse": [{"matcher": "*", **ORCA}], "Stop": [ORCA]},
    }
    settings.write_text(json.dumps(original, indent=2) + "\n")

    preview = hook_install.write_claude_hooks(settings, HookOptions(home=tmp_path, dry_run=True))
    assert preview["action"] == "change"
    assert preview["events"] == list(doctor_harness.CLAUDE_HOOK_EVENTS)
    assert json.loads(settings.read_text()) == original

    hook_install.write_claude_hooks(settings, HookOptions(home=tmp_path))
    once = settings.read_text()
    assert (
        hook_install.write_claude_hooks(settings, HookOptions(home=tmp_path))["action"]
        == "unchanged"
    )
    assert settings.read_text() == once
    table = json.loads(once)["hooks"]
    assert set(table) >= set(doctor_harness.CLAUDE_HOOK_EVENTS)
    assert table["PreToolUse"][0] == {"matcher": "*", **ORCA}
    assert table["Stop"][0] == ORCA
    assert sum(hook_install.is_owned(entry) for entry in table["PreToolUse"]) == 1
    shim = tmp_path / doctor_harness.MANAGED_RUNTIME / doctor_harness.SHIM
    command = doctor_harness.claude_hook_command(shim, "PreToolUse")
    assert table["PreToolUse"][1]["hooks"][0]["command"] == command
    assert len(list(tmp_path.glob("settings.json.bak-*"))) == 1

    removed = hook_install.write_claude_hooks(settings, HookOptions(home=tmp_path), remove=True)
    assert removed["action"] == "remove"
    assert json.loads(settings.read_text()) == original


def test_claude_hooks_write_through_a_symlinked_settings_file(tmp_path: Path) -> None:
    target = tmp_path / "managed" / "settings.json"
    target.parent.mkdir()
    target.write_text('{"env": {"A": "1"}}\n')
    link = tmp_path / "settings.json"
    link.symlink_to(target)

    hook_install.write_claude_hooks(link, HookOptions(home=tmp_path))

    assert link.is_symlink()
    assert set(json.loads(target.read_text())["hooks"]) >= set(doctor_harness.CLAUDE_HOOK_EVENTS)


def test_a_settings_file_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text('{"hooks": []}')

    with pytest.raises(HookInstallError, match=r"settings\.hooks must be an object"):
        hook_install.write_claude_hooks(settings, HookOptions(home=tmp_path))


def test_omp_links_the_managed_plugin_once_and_uninstalls_it(fake_omp: FakeOmp) -> None:
    home = Path.home()
    omp = fake_omp
    run = HookOptions(home=home)
    hook_install.install_runtime(run, "omp")

    assert hook_install.link_omp_plugin(HookOptions(home=home, dry_run=True)) == {
        "plugin": "agent-introspection-activity",
        "action": "link",
    }
    assert omp.calls == []
    assert hook_install.link_omp_plugin(run)["action"] == "link"
    assert hook_install.link_omp_plugin(run)["action"] == "unchanged"
    managed = home / doctor_harness.MANAGED_RUNTIME / "omp"
    assert omp.calls == [["plugin", "link", str(managed)]]
    assert doctor_harness.omp_plugin_check(home)["status"] == "pass"

    assert hook_install.link_omp_plugin(run, remove=True)["action"] == "remove"
    assert omp.calls[-1] == ["plugin", "uninstall", "agent-introspection-activity"]


def test_omp_hooks_need_omp_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hook_install, "_which", lambda _name: None)

    with pytest.raises(HookInstallError, match="omp is not on PATH"):
        hook_install.link_omp_plugin(HookOptions(home=tmp_path))


def test_configure_installs_the_hooks_and_doctor_then_passes_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    (tmp_path / ".claude").mkdir()

    preview = harness_config.configure(
        "claude-code", None, harness_config.ConfigureOptions(dry_run=True)
    )
    assert preview["hooks"]["settings"][0]["action"] == "add"
    assert not (tmp_path / doctor_harness.MANAGED_RUNTIME).exists()

    result = harness_config.configure("claude-code", None, harness_config.ConfigureOptions())

    assert [item["action"] for item in result["hooks"]["runtime"]] == ["add", "unchanged"]
    settings = json.loads((tmp_path / ".claude/settings.json").read_text())
    assert set(settings["hooks"]) == set(doctor_harness.CLAUDE_HOOK_EVENTS)
    assert settings["env"]["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1"
    assert doctor_harness.shim_check(tmp_path, "claude-code")["status"] == "pass"
    again = harness_config.configure("claude-code", None, harness_config.ConfigureOptions())
    assert again["hooks"]["settings"][0]["action"] == "unchanged"
    assert again["next_steps"] == []

    removed = harness_config.remove_hooks("claude-code")
    assert removed["hooks"]["settings"][0]["action"] == "remove"
    assert "hooks" not in json.loads((tmp_path / ".claude/settings.json").read_text())


def test_a_stale_shim_warns_with_the_command_that_updates_it(tmp_path: Path) -> None:
    hook_install.install_runtime(HookOptions(home=tmp_path), "omp")
    shim = tmp_path / doctor_harness.MANAGED_RUNTIME / doctor_harness.SHIM
    shim.write_text(shim.read_text() + "# older\n")

    stale = doctor_harness.shim_check(tmp_path, "omp")

    assert stale["status"] == "warn"
    assert stale["fix"] == "`agent-introspection harness configure omp` updates it"


def test_hooks_go_into_every_root_and_remove_hooks_reaches_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    roots = (tmp_path / "work-claude", tmp_path / "home-claude")
    for root in roots:
        root.mkdir()
    options = harness_config.ConfigureOptions(roots=tuple(map(str, roots)))

    result = harness_config.configure("claude-code", None, options)

    written = [item["path"] for item in result["hooks"]["settings"]]
    assert written == [str((root / "settings.json").resolve()) for root in roots]
    extra = roots[1]
    assert "hooks" in json.loads((extra / "settings.json").read_text())
    removed = harness_config.remove_hooks("claude-code", roots=tuple(map(str, roots)))
    assert [item["action"] for item in removed["hooks"]["settings"]] == ["remove", "remove"]
    assert "hooks" not in json.loads((extra / "settings.json").read_text())


def test_omp_missing_stops_configure_before_any_file_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(hook_install, "_which", lambda _name: None)

    for dry_run in (True, False):
        with pytest.raises(HookInstallError, match="omp is not on PATH"):
            harness_config.configure("omp", None, harness_config.ConfigureOptions(dry_run=dry_run))
    assert not (tmp_path / ".omp").exists()


def test_a_hung_omp_is_reported_as_a_hook_install_error(tmp_path: Path) -> None:
    def hang(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(list(command), 60)

    with pytest.raises(HookInstallError, match=r"`omp plugin link .*` failed"):
        hook_install.link_omp_plugin(HookOptions(home=tmp_path, run=hang))


def test_a_selected_config_is_pointed_at_and_the_default_removes_the_pointer(
    tmp_path: Path,
) -> None:
    pointer = tmp_path / doctor_harness.MANAGED_RUNTIME / doctor_harness.CONFIG_POINTER
    selected = tmp_path / "custom.toml"

    hook_install.install_runtime(HookOptions(home=tmp_path, config_path=selected), "claude-code")

    assert pointer.read_text() == f"{selected.resolve()}\n"
    assert doctor_harness.pointer_check(tmp_path, "omp", selected)["status"] == "pass"
    stale = doctor_harness.pointer_check(tmp_path, "omp", None)
    assert (stale["status"], stale["fix"]) == (
        "warn",
        "`agent-introspection harness configure omp` points them at it",
    )
    default = HookOptions(home=tmp_path, config_path=config.DEFAULT_CONFIG_PATH)
    assert hook_install.install_runtime(default, "claude-code")[-1]["action"] == "remove"
    assert not pointer.exists()
    assert doctor_harness.pointer_check(tmp_path, "omp", None)["status"] == "pass"


def test_the_shim_exports_the_pointed_config_unless_one_is_set(tmp_path: Path) -> None:
    hook_install.install_runtime(
        HookOptions(home=tmp_path, config_path=tmp_path / "c.toml"), "claude-code"
    )
    shim = tmp_path / doctor_harness.MANAGED_RUNTIME / doctor_harness.SHIM
    seen = tmp_path / "seen"
    fake = tmp_path / "bin/agent-introspection"
    fake.parent.mkdir()
    fake.write_text(f'#!/bin/sh\nprintf %s "$AGENT_INTROSPECTION_CONFIG" > "{seen}"\n')
    fake.chmod(0o755)

    for preset, expected in ((None, str((tmp_path / "c.toml").resolve())), ("/x.toml", "/x.toml")):
        seen.unlink(missing_ok=True)
        environment = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "AGENT_INTROSPECTION_BIN": str(fake),
        }
        if preset is not None:
            environment["AGENT_INTROSPECTION_CONFIG"] = preset
        subprocess.run(
            ["/bin/sh", str(shim), "omp", "tool_call"],
            input="{}",
            env=environment,
            text=True,
            timeout=10,
            check=True,
        )
        for _ in range(100):
            if seen.exists():
                break
            time.sleep(0.05)
        assert seen.read_text() == expected

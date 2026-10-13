import getpass
import os
import plistlib
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_introspection import facts, schedule
from agent_introspection.config import (
    AppConfig,
    ConfigurationError,
    MachineConfig,
    MachineRole,
    SigNozConfig,
)

EXECUTABLE = Path("/venv/bin/agent-introspection")


def _result(code: int = 0, stderr: str = "", stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess((), code, stdout, stderr)


def _never(*args: str) -> subprocess.CompletedProcess[str]:
    pytest.fail(f"scheduler command ran: {args}")


@dataclass
class Launchd:
    """A launchctl stand-in that tracks which labels are loaded."""

    loaded: set[str] = field(default_factory=set)
    # stderr for a plist whose bootstrap fails, or "" when it loads.
    refuse: Callable[[bytes], str] = lambda _plist: ""
    stuck: bool = False
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def __call__(self, *args: str) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        if args[0] == "list":
            return _result(0 if args[1] in self.loaded else 113)
        if args[0] == "bootout":
            label = args[1].rsplit("/", 1)[1]
            if label not in self.loaded:
                return _result(3, "No such process")
            if self.stuck:
                return _result(5, "Operation not permitted")
            self.loaded.discard(label)
            return _result()
        return self._bootstrap(Path(args[2]).read_bytes())

    def _bootstrap(self, content: bytes) -> subprocess.CompletedProcess[str]:
        if error := self.refuse(content):
            return _result(5, error)
        label = plistlib.loads(content)["Label"]
        if label in self.loaded:
            # launchctl refuses to load a label that is already loaded.
            return _result(37, "Bootstrap failed: 37: Operation already in progress")
        self.loaded.add(label)
        return _result()


@dataclass
class Systemd:
    """A ``systemctl --user`` stand-in; ``fail`` maps a verb to its stderr.

    ``enabled`` defaults to ``active``: a running timer is an enabled one.
    """

    fail: dict[str, str] = field(default_factory=dict)
    active: bool = False
    enabled: bool | None = None
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def __call__(self, *args: str) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        verb = args[0]
        if verb in self.fail:
            return _result(1, self.fail[verb])
        if verb == "is-active":
            return _result(0 if self.active else 3, stdout="active" if self.active else "inactive")
        if verb == "is-enabled":
            enabled = self.active if self.enabled is None else self.enabled
            return _result(0 if enabled else 1, stdout="enabled" if enabled else "disabled")
        return _result()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inbox: Path) -> Path:
    """Point every job, unit, backup, and spool path into ``tmp_path``; no scheduler may run."""
    agents = tmp_path / "LaunchAgents"
    units = tmp_path / "systemd-user"
    monkeypatch.setattr(schedule, "PLIST", agents / schedule.PLIST.name)
    monkeypatch.setattr(schedule, "LEGACY_PLIST", agents / schedule.LEGACY_PLIST.name)
    monkeypatch.setattr(schedule, "SYSTEMD_UNITS", units)
    monkeypatch.setattr(schedule, "SERVICE", units / schedule.SERVICE.name)
    monkeypatch.setattr(schedule, "TIMER", units / schedule.TIMER.name)
    monkeypatch.setattr(schedule, "SPOOL_SYNC_SERVICE", units / schedule.SPOOL_SYNC_SERVICE.name)
    monkeypatch.setattr(schedule, "SPOOL_SYNC_TIMER", units / schedule.SPOOL_SYNC_TIMER.name)
    monkeypatch.setattr(schedule, "BACKUPS", tmp_path / "backups")
    monkeypatch.setattr(schedule, "LINGER", tmp_path / "linger")
    monkeypatch.setattr(schedule, "_launchctl", _never)
    monkeypatch.setattr(schedule, "_systemctl", _never)
    (tmp_path / "agent-introspection").write_text("")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
    monkeypatch.setattr(schedule, "load_config", lambda _config: AppConfig())
    monkeypatch.setattr(schedule, "_job_path", lambda _config: ["/opt/tools"])
    return tmp_path


def _on(monkeypatch: pytest.MonkeyPatch, platform: str, scheduler: Launchd | Systemd) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    name = "_launchctl" if isinstance(scheduler, Launchd) else "_systemctl"
    monkeypatch.setattr(schedule, name, scheduler)


def _legacy_plist() -> bytes:
    document = plistlib.loads(schedule.plist(EXECUTABLE, ["/usr/bin"]))
    return plistlib.dumps(document | {"Label": schedule.LEGACY_LABEL})


def test_launchd_job_runs_the_sync_every_minute_under_a_neutral_label() -> None:
    document = plistlib.loads(schedule.plist(EXECUTABLE, ["/opt/docker/bin", "/usr/bin"]))

    assert document["Label"] == schedule.LABEL == "dev.agent-introspection.sync"
    assert document["ProgramArguments"] == [str(EXECUTABLE), "facts", "sync", "--log"]
    assert document["StartInterval"] == 60
    assert document["EnvironmentVariables"]["PATH"] == "/opt/docker/bin:/usr/bin"


def test_launchd_job_keeps_a_selected_configuration() -> None:
    document = plistlib.loads(schedule.plist(EXECUTABLE, ["/usr/bin"], Path("/etc/ai/custom.toml")))

    assert document["ProgramArguments"] == [
        str(EXECUTABLE),
        "--config",
        "/etc/ai/custom.toml",
        "facts",
        "sync",
        "--log",
    ]


def test_systemd_units_run_the_same_sync_every_minute_with_the_same_path_and_config() -> None:
    service = schedule.service_unit(
        Path("/home/me/.local/bin/agent-introspection"),
        ["/opt/docker/bin", "/usr/bin", "/opt/docker/bin"],
        Path("/home/me/My Config/50%$HOME.toml"),
    )
    timer = schedule.timer_unit()

    assert 'Environment="PATH=/opt/docker/bin:/usr/bin"\n' in service
    # Each argument is quoted, `%` and `$` are literal, and --log writes the sync log.
    assert (
        'ExecStart="/home/me/.local/bin/agent-introspection" "--config" '
        '"/home/me/My Config/50%%$$HOME.toml" "facts" "sync" "--log"\n'
    ) in service
    assert "Type=oneshot\n" in service
    assert "OnCalendar=minutely\n" in timer
    assert "Persistent=true\n" in timer
    assert f"Unit={schedule.SERVICE.name}\n" in timer
    assert "WantedBy=timers.target\n" in timer


def test_host_units_do_not_collide_with_the_spool_sync_units_of_other_machines() -> None:
    bundled = Path(__file__).parents[1] / ".agents/skills/introspection-onboarding/scripts"
    names = {path.name for path in bundled.glob("*.service")} | {
        path.name for path in bundled.glob("*.timer")
    }

    assert names == {schedule.SPOOL_SYNC_SERVICE.name, schedule.SPOOL_SYNC_TIMER.name}
    assert not names & {schedule.SERVICE.name, schedule.TIMER.name}


def test_scheduled_job_path_follows_the_connection_mode_and_key_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    found = {
        "docker": "/opt/docker/bin/docker",
        "security": "/usr/bin/security",
        "pass": "/opt/pass/bin/pass",
        "omp": "/opt/omp/bin/omp",
    }
    monkeypatch.setattr(shutil, "which", found.get)
    command = SigNozConfig(
        clickhouse_url="http://localhost:8123", clickhouse_password_command=("security", "-w")
    )

    # Without a key command, omp's directory serves the stored-credential fallback.
    assert schedule._job_path(AppConfig()) == ["/opt/docker/bin", "/opt/omp/bin"]
    assert schedule._job_path(AppConfig(signoz=command)) == ["/usr/bin", "/opt/omp/bin"]
    keyed = AppConfig(labels_openrouter_key_command=("pass", "show", "openrouter"))
    assert schedule._job_path(keyed) == ["/opt/docker/bin", "/opt/pass/bin"]

    variable = SigNozConfig(clickhouse_url="http://localhost:8123", clickhouse_password_env="CH")
    with pytest.raises(RuntimeError, match="clickhouse_password_command"):
        schedule._job_path(AppConfig(signoz=variable))
    missing = AppConfig(labels_openrouter_key_command=("op", "read", "x"))
    with pytest.raises(RuntimeError, match="op not found on PATH"):
        schedule._job_path(missing)


def test_password_command_output_is_the_password_and_never_in_errors() -> None:
    from agent_introspection.config import ConfigurationError

    ok = SigNozConfig(clickhouse_url="https://ch", clickhouse_password_command=("echo", "s3cret"))
    assert facts.clickhouse_password(ok) == "s3cret"
    failing = SigNozConfig(
        clickhouse_url="https://ch", clickhouse_password_command=("sh", "-c", "echo leak; exit 3")
    )
    with pytest.raises(ConfigurationError) as error:
        facts.clickhouse_password(failing)
    assert "leak" not in str(error.value)


@pytest.mark.parametrize("platform", ["win32", "cygwin", "freebsd14"])
def test_other_platforms_are_refused_by_name(
    monkeypatch: pytest.MonkeyPatch, home: Path, platform: str
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    for action in (schedule.schedule_install, schedule.schedule_remove, schedule.schedule_status):
        with pytest.raises(RuntimeError, match=f"macOS \\(launchd\\) and Linux.*not {platform}"):
            action()


def test_launchd_install_retires_the_legacy_job_before_loading_the_new_one(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    schedule.LEGACY_PLIST.parent.mkdir(parents=True)
    schedule.LEGACY_PLIST.write_bytes(_legacy_plist())
    launchd = Launchd(loaded={schedule.LEGACY_LABEL})
    _on(monkeypatch, "darwin", launchd)

    result = schedule.schedule_install()

    assert launchd.loaded == {schedule.LABEL}
    assert not schedule.LEGACY_PLIST.exists()
    backup = Path(result["legacy"]["backup"])
    assert backup.parent == schedule.BACKUPS
    assert backup.name.startswith(f"{schedule.LEGACY_PLIST.name}.bak-")
    assert backup.read_bytes() == _legacy_plist()
    assert result["legacy"] == {
        "label": schedule.LEGACY_LABEL,
        "unloaded": True,
        "backup": str(backup),
    }
    assert (result["label"], result["scheduler"]) == (schedule.LABEL, "launchd")
    verbs = [(call[0], call[-1].rsplit("/", 1)[-1]) for call in launchd.calls]
    stop_legacy = verbs.index(("bootout", schedule.LEGACY_LABEL))
    assert stop_legacy < verbs.index(("bootstrap", schedule.PLIST.name))
    path = plistlib.loads(schedule.PLIST.read_bytes())["EnvironmentVariables"]["PATH"]
    assert path.split(":") == ["/opt/tools", *schedule.SYSTEM_PATH]


def test_launchd_install_without_a_legacy_job_reports_none(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    launchd = Launchd()
    _on(monkeypatch, "darwin", launchd)

    assert schedule.schedule_install()["legacy"] is None
    assert launchd.loaded == {schedule.LABEL}
    assert not schedule.BACKUPS.exists()


def test_launchd_reinstall_stops_the_loaded_job_before_loading_the_new_one(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    schedule.PLIST.parent.mkdir(parents=True)
    schedule.PLIST.write_bytes(schedule.plist(Path("/old/agent-introspection"), ["/usr/bin"]))
    launchd = Launchd(loaded={schedule.LABEL})
    _on(monkeypatch, "darwin", launchd)

    result = schedule.schedule_install()

    assert (result["installed"], result["legacy"]) == (True, None)
    assert launchd.loaded == {schedule.LABEL}
    verbs = [(call[0], call[-1].rsplit("/", 1)[-1]) for call in launchd.calls]
    assert verbs.index(("bootout", schedule.LABEL)) < verbs.index((
        "bootstrap",
        schedule.PLIST.name,
    ))
    arguments = plistlib.loads(schedule.PLIST.read_bytes())["ProgramArguments"]
    assert arguments[0] == str(home / "agent-introspection")


_STILL_RUNNING = (
    rf"^could not stop the job {re.escape(schedule.LABEL)}: Operation not permitted; "
    "it is still loaded, its plist unchanged$"
)


@pytest.mark.parametrize(
    ("job", "change", "error"),
    [
        ("legacy", "install", r"could not stop the legacy job.*not permitted"),
        ("current", "install", _STILL_RUNNING),
        # A loaded job whose plist is gone is not given one it would never use.
        ("current without plist", "install", _STILL_RUNNING),
        ("current", "remove", _STILL_RUNNING),
    ],
    ids=["legacy-install", "reinstall", "reinstall-without-plist", "remove"],
)
def test_a_loaded_job_that_cannot_be_stopped_blocks_the_change_and_is_left_as_it_was(
    monkeypatch: pytest.MonkeyPatch, home: Path, job: str, change: str, error: str
) -> None:
    # `launchctl bootout` fails outside the user's GUI session (over SSH, say): the job
    # keeps running, so the command must neither report it stopped nor change its files.
    label, path, content = (
        (schedule.LEGACY_LABEL, schedule.LEGACY_PLIST, _legacy_plist())
        if job == "legacy"
        else (schedule.LABEL, schedule.PLIST, schedule.plist(Path("/old/ai"), ["/usr/bin"]))
    )
    path.parent.mkdir(parents=True)
    if job != "current without plist":
        path.write_bytes(content)
    plists = (schedule.PLIST, schedule.LEGACY_PLIST)
    before = {plist: plist.read_bytes() if plist.exists() else None for plist in plists}
    launchd = Launchd(loaded={label}, stuck=True)
    _on(monkeypatch, "darwin", launchd)

    with pytest.raises(RuntimeError, match=error):
        getattr(schedule, f"schedule_{change}")()
    assert launchd.loaded == {label}
    assert {plist: plist.read_bytes() if plist.exists() else None for plist in plists} == before
    assert "bootstrap" not in [call[0] for call in launchd.calls]
    assert not schedule.BACKUPS.exists()


def test_a_job_that_cannot_be_stopped_after_the_legacy_plist_was_retired_says_so(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    # The legacy job was not loaded, so install moved its plist away before it found
    # the current job stuck; putting it back would not restore anything that ran.
    previous = schedule.plist(Path("/old/ai"), ["/usr/bin"])
    schedule.PLIST.parent.mkdir(parents=True)
    schedule.PLIST.write_bytes(previous)
    schedule.LEGACY_PLIST.write_bytes(_legacy_plist())
    launchd = Launchd(loaded={schedule.LABEL}, stuck=True)
    _on(monkeypatch, "darwin", launchd)

    with pytest.raises(RuntimeError) as raised:
        schedule.schedule_install()
    (backup,) = schedule.BACKUPS.iterdir()
    assert str(raised.value) == (
        f"could not stop the job {schedule.LABEL}: Operation not permitted; it is still "
        f"loaded, its plist unchanged; the legacy job {schedule.LEGACY_LABEL} was retired "
        f"first, its plist moved to {backup}"
    )
    assert backup.read_bytes() == _legacy_plist()
    assert launchd.loaded == {schedule.LABEL}
    assert schedule.PLIST.read_bytes() == previous


def test_failed_bootstrap_restores_and_reloads_the_previous_job(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    previous = schedule.plist(Path("/old/agent-introspection"), ["/usr/bin"])
    schedule.PLIST.parent.mkdir(parents=True)
    schedule.PLIST.write_bytes(previous)
    launchd = Launchd(
        loaded={schedule.LABEL}, refuse=lambda plist: "" if plist == previous else "boom"
    )
    _on(monkeypatch, "darwin", launchd)

    with pytest.raises(RuntimeError, match="boom"):
        schedule.schedule_install()
    assert schedule.PLIST.read_bytes() == previous
    assert launchd.calls[-1] == ("bootstrap", f"gui/{os.getuid()}", str(schedule.PLIST))
    assert launchd.loaded == {schedule.LABEL}


def test_failed_bootstrap_after_a_migration_brings_the_legacy_job_back(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    schedule.LEGACY_PLIST.parent.mkdir(parents=True)
    schedule.LEGACY_PLIST.write_bytes(_legacy_plist())
    launchd = Launchd(
        loaded={schedule.LEGACY_LABEL},
        refuse=lambda plist: "" if plist == _legacy_plist() else "boom",
    )
    _on(monkeypatch, "darwin", launchd)

    with pytest.raises(RuntimeError, match=r"boom; .* is back in place and loaded"):
        schedule.schedule_install()
    assert launchd.loaded == {schedule.LEGACY_LABEL}
    assert schedule.LEGACY_PLIST.read_bytes() == _legacy_plist()
    assert not schedule.PLIST.exists()
    assert not list(schedule.BACKUPS.iterdir())


def test_failed_bootstrap_puts_back_a_legacy_job_that_was_not_loaded_unloaded(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    # The user stopped the legacy job; a failed install must not start it again.
    schedule.LEGACY_PLIST.parent.mkdir(parents=True)
    schedule.LEGACY_PLIST.write_bytes(_legacy_plist())
    launchd = Launchd(refuse=lambda plist: "" if plist == _legacy_plist() else "boom")
    _on(monkeypatch, "darwin", launchd)

    with pytest.raises(RuntimeError, match=r"boom; .* is back in place, not loaded, as it was"):
        schedule.schedule_install()
    assert launchd.loaded == set()
    assert schedule.LEGACY_PLIST.read_bytes() == _legacy_plist()
    assert not schedule.PLIST.exists()
    assert not list(schedule.BACKUPS.iterdir())


def test_failed_bootstrap_puts_back_a_previous_job_that_was_not_loaded_unloaded(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    # The user booted the job out to pause syncing; a failed reinstall must not
    # start it again.
    previous = schedule.plist(Path("/old/agent-introspection"), ["/usr/bin"])
    schedule.PLIST.parent.mkdir(parents=True)
    schedule.PLIST.write_bytes(previous)
    launchd = Launchd(refuse=lambda plist: "" if plist == previous else "boom")
    _on(monkeypatch, "darwin", launchd)

    with pytest.raises(
        RuntimeError, match=rf"boom; {schedule.PLIST.name} is back in place, not loaded, as it was"
    ):
        schedule.schedule_install()
    assert launchd.loaded == set()
    assert schedule.PLIST.read_bytes() == previous
    assert [call[0] for call in launchd.calls].count("bootstrap") == 1


def test_failed_reload_of_the_previous_job_reports_both_failures(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    previous = schedule.plist(Path("/old/agent-introspection"), ["/usr/bin"])
    schedule.PLIST.parent.mkdir(parents=True)
    schedule.PLIST.write_bytes(previous)
    launchd = Launchd(
        loaded={schedule.LABEL},
        refuse=lambda plist: "old-boom" if plist == previous else "new-boom",
    )
    _on(monkeypatch, "darwin", launchd)

    with pytest.raises(RuntimeError) as raised:
        schedule.schedule_install()
    text = str(raised.value)
    assert "new-boom" in text
    assert "old-boom" in text
    assert "stopped" in text
    assert schedule.PLIST.read_bytes() == previous


def test_missing_executable_is_rejected_before_the_loaded_job_is_stopped(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    (home / "agent-introspection").unlink()
    for platform in ("darwin", "linux"):
        monkeypatch.setattr(sys, "platform", platform)
        with pytest.raises(RuntimeError, match="not found"):
            schedule.schedule_install()


def test_an_additional_machine_is_refused_before_any_job_changes(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    additional = MachineConfig(MachineRole.ADDITIONAL, "http://signoz-host.example:4318")
    monkeypatch.setattr(schedule, "load_config", lambda _config: AppConfig(machine=additional))
    for platform in ("darwin", "linux"):
        monkeypatch.setattr(sys, "platform", platform)
        with pytest.raises(ConfigurationError, match="needs SigNoz's ClickHouse, which this addi"):
            schedule.schedule_install()
    assert not schedule.PLIST.exists()
    assert not schedule.SYSTEMD_UNITS.exists()


def test_launchd_remove_unloads_and_retires_both_jobs(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    schedule.PLIST.parent.mkdir(parents=True)
    schedule.PLIST.write_bytes(schedule.plist(EXECUTABLE, ["/usr/bin"]))
    schedule.LEGACY_PLIST.write_bytes(_legacy_plist())
    launchd = Launchd(loaded={schedule.LABEL, schedule.LEGACY_LABEL})
    _on(monkeypatch, "darwin", launchd)

    result = schedule.schedule_remove()

    assert launchd.loaded == set()
    assert not schedule.PLIST.exists()
    assert not schedule.LEGACY_PLIST.exists()
    assert result["removed"] is True
    assert result["legacy"]["unloaded"] is True
    assert Path(result["legacy"]["backup"]).read_bytes() == _legacy_plist()


def test_launchd_status_reports_the_job_and_any_legacy_job(
    monkeypatch: pytest.MonkeyPatch, home: Path, inbox: Path
) -> None:
    _on(monkeypatch, "darwin", Launchd(loaded={schedule.LEGACY_LABEL}))
    schedule.LEGACY_PLIST.parent.mkdir(parents=True)
    schedule.LEGACY_PLIST.write_bytes(_legacy_plist())
    for name in ("a.json", "b.json", "rejected/c.json"):
        (inbox / name).parent.mkdir(parents=True, exist_ok=True)
        (inbox / name).write_text("{}")

    status = schedule.schedule_status()

    assert (status["scheduler"], status["installed"], status["loaded"]) == ("launchd", False, False)
    # The spool counts come from the test's own spool, never the real home's.
    assert (status["inbox_backlog"], status["inbox_rejected"]) == (2, 1)
    assert status["legacy"] == {
        "label": schedule.LEGACY_LABEL,
        "installed": True,
        "loaded": True,
    }
    schedule.LEGACY_PLIST.unlink()
    _on(monkeypatch, "darwin", Launchd(loaded={schedule.LABEL}))
    assert schedule.schedule_status()["legacy"] is None
    assert schedule.schedule_status()["loaded"] is True


def test_systemd_install_backs_up_writes_and_starts_the_timer(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    schedule.SYSTEMD_UNITS.mkdir(parents=True)
    schedule.SERVICE.write_text("old service")
    systemd = Systemd()
    _on(monkeypatch, "linux", systemd)
    (home / "linger").mkdir()
    (home / "linger" / getpass.getuser()).write_text("")

    result = schedule.schedule_install(Path("/etc/ai/custom.toml"))

    assert systemd.calls == [("daemon-reload",), ("enable", "--now", schedule.TIMER.name)]
    assert schedule.SERVICE.read_text() == schedule.service_unit(
        home / "agent-introspection",
        ["/opt/tools", *schedule.SYSTEM_PATH],
        Path("/etc/ai/custom.toml"),
    )
    assert schedule.TIMER.read_text() == schedule.timer_unit()
    (backup,) = result["backups"]
    assert Path(backup).parent == schedule.BACKUPS
    assert Path(backup).read_text() == "old service"
    assert (result["scheduler"], result["unit"], result["linger"]) == (
        "systemd",
        schedule.TIMER.name,
        True,
    )


def test_systemd_install_that_fails_to_start_restores_and_restarts_the_previous_units(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    schedule.SYSTEMD_UNITS.mkdir(parents=True)
    schedule.SERVICE.write_text("old service")
    schedule.TIMER.write_text("old timer")
    systemd = Systemd(active=True)

    def enable_only_the_old_units(*args: str) -> subprocess.CompletedProcess[str]:
        systemd.fail = {} if schedule.TIMER.read_text() == "old timer" else {"enable": "bad unit"}
        return systemd(*args)

    _on(monkeypatch, "linux", systemd)
    monkeypatch.setattr(schedule, "_systemctl", enable_only_the_old_units)

    with pytest.raises(RuntimeError, match=r"bad unit; .*\.timer is back in place and active"):
        schedule.schedule_install()
    assert (schedule.SERVICE.read_text(), schedule.TIMER.read_text()) == (
        "old service",
        "old timer",
    )
    assert systemd.calls[-3:] == [
        ("daemon-reload",),
        ("enable", schedule.TIMER.name),
        ("start", schedule.TIMER.name),
    ]


@pytest.mark.parametrize(
    ("enabled", "kept"),
    [(True, ("enable", schedule.TIMER.name)), (False, ("disable", schedule.TIMER.name))],
)
def test_systemd_install_that_fails_puts_back_a_stopped_timer_stopped(
    monkeypatch: pytest.MonkeyPatch, home: Path, enabled: bool, kept: tuple[str, ...]
) -> None:
    # The user stopped (or disabled) the timer to pause syncing; a failed reinstall
    # must not start it again, nor change whether it starts at login.
    schedule.SYSTEMD_UNITS.mkdir(parents=True)
    schedule.SERVICE.write_text("old service")
    schedule.TIMER.write_text("old timer")
    systemd = Systemd(active=False, enabled=enabled)

    def enable_only_the_old_units(*args: str) -> subprocess.CompletedProcess[str]:
        systemd.fail = {} if schedule.TIMER.read_text() == "old timer" else {"enable": "bad unit"}
        return systemd(*args)

    _on(monkeypatch, "linux", systemd)
    monkeypatch.setattr(schedule, "_systemctl", enable_only_the_old_units)

    with pytest.raises(RuntimeError, match=r"bad unit; .*\.timer is back in place, not active"):
        schedule.schedule_install()
    assert schedule.TIMER.read_text() == "old timer"
    failed = systemd.calls.index(("enable", "--now", schedule.TIMER.name))
    assert systemd.calls[failed + 1 :] == [
        ("daemon-reload",),
        kept,
        ("stop", schedule.TIMER.name),
    ]


def test_systemd_install_that_fails_with_no_previous_units_leaves_nothing_enabled(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    systemd = Systemd(fail={"enable": "bad unit"})
    _on(monkeypatch, "linux", systemd)

    with pytest.raises(RuntimeError, match="bad unit"):
        schedule.schedule_install()
    assert not schedule.SERVICE.exists()
    assert not schedule.TIMER.exists()
    assert ("disable", "--now", schedule.TIMER.name) in systemd.calls
    assert systemd.calls[-1] == ("daemon-reload",)


def test_systemd_install_that_replaced_only_the_service_puts_it_back_with_no_timer(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    schedule.SYSTEMD_UNITS.mkdir(parents=True)
    schedule.SERVICE.write_text("old service")
    systemd = Systemd(fail={"enable": "bad unit"})
    _on(monkeypatch, "linux", systemd)

    with pytest.raises(RuntimeError, match=r"^bad unit$"):
        schedule.schedule_install()
    assert schedule.SERVICE.read_text() == "old service"
    assert not schedule.TIMER.exists()
    assert ("disable", "--now", schedule.TIMER.name) in systemd.calls
    assert systemd.calls[-1] == ("daemon-reload",)


def test_systemd_restart_failure_after_a_failed_install_reports_both(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    schedule.SYSTEMD_UNITS.mkdir(parents=True)
    schedule.SERVICE.write_text("old service")
    schedule.TIMER.write_text("old timer")
    _on(monkeypatch, "linux", Systemd(active=True, fail={"enable": "bad unit"}))

    with pytest.raises(RuntimeError, match=r"bad unit.*could not be restarted.*stopped"):
        schedule.schedule_install()


def test_systemd_remove_and_status(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    schedule.SYSTEMD_UNITS.mkdir(parents=True)
    schedule.SERVICE.write_text("service")
    schedule.TIMER.write_text("timer")
    systemd = Systemd(active=True)
    _on(monkeypatch, "linux", systemd)

    status = schedule.schedule_status()
    assert {key: status[key] for key in ("scheduler", "installed", "enabled", "loaded")} == {
        "scheduler": "systemd",
        "installed": True,
        "enabled": True,
        "loaded": True,
    }
    assert status["linger"] is False
    assert (status["inbox_backlog"], status["inbox_rejected"]) == (0, 0)

    assert schedule.schedule_remove() == {
        "removed": True,
        "scheduler": "systemd",
        "unit": schedule.TIMER.name,
    }
    assert not schedule.SERVICE.exists()
    assert not schedule.TIMER.exists()
    assert systemd.calls[-2:] == [("disable", "--now", schedule.TIMER.name), ("daemon-reload",)]
    systemd.active = False
    assert schedule.schedule_status()["installed"] is False


def test_systemd_remove_refuses_when_a_scheduled_timer_cannot_be_stopped(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    schedule.SYSTEMD_UNITS.mkdir(parents=True)
    schedule.SERVICE.write_text("service")
    schedule.TIMER.write_text("timer")
    systemd = Systemd(active=True, fail={"disable": "Failed to connect to bus"})
    _on(monkeypatch, "linux", systemd)

    with pytest.raises(RuntimeError, match=r"Failed to connect to bus.*still scheduled"):
        schedule.schedule_remove()

    assert schedule.SERVICE.read_text() == "service"
    assert schedule.TIMER.read_text() == "timer"
    assert ("daemon-reload",) not in systemd.calls


def test_systemd_remove_ignores_a_failed_stop_of_a_timer_that_is_not_scheduled(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    schedule.SYSTEMD_UNITS.mkdir(parents=True)
    schedule.SERVICE.write_text("service")
    schedule.TIMER.write_text("timer")
    _on(monkeypatch, "linux", Systemd(active=False, fail={"disable": "Unit not loaded"}))

    assert schedule.schedule_remove()["removed"] is True
    assert not schedule.SERVICE.exists()
    assert not schedule.TIMER.exists()


def test_sync_log_lines_carry_a_utc_timestamp(tmp_path: Path) -> None:
    log = tmp_path / "sync.log"
    now = datetime(2026, 10, 12, 8, 30, 5, tzinfo=UTC)

    schedule.log_line('{"synced":1}', log, now)
    schedule.log_line("FactsError: first\nsecond line", log, now)

    assert log.read_text().splitlines() == [
        '2026-10-12T08:30:05Z {"synced":1}',
        "2026-10-12T08:30:05Z FactsError: first",
        "2026-10-12T08:30:05Z second line",
    ]


def test_sync_log_rotates_by_size_and_keeps_a_bounded_number_of_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "sync.log"
    monkeypatch.setattr(schedule, "LOG_MAX_BYTES", 100)
    for index in range(12):
        schedule.log_line(f"line {index} " + "x" * 40, log)

    files = sorted(path.name for path in tmp_path.iterdir())
    assert files == ["sync.log", "sync.log.1", "sync.log.2"]
    assert len(files) == schedule.LOG_FILES
    kept = "".join((tmp_path / name).read_text() for name in files)
    # The newest lines survive; only the oldest are dropped.
    assert "line 11 " in kept
    assert "line 0 " not in kept
    assert all((tmp_path / name).stat().st_size <= 100 + 80 for name in files)


def test_an_unwritable_sync_log_falls_back_to_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")

    schedule.log_line("kept", blocked / "sync.log")

    assert "kept" in capsys.readouterr().err


def test_the_spool_sync_timer_of_an_additional_machine_is_bundled_for_linux_only(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    systemd = Systemd(active=True)
    _on(monkeypatch, "linux", systemd)
    timer = schedule.SPOOL_SYNC_TIMER.name

    assert schedule.spool_sync_status() == {
        "scheduler": "systemd",
        "unit": timer,
        "installed": False,
        "enabled": True,
        "loaded": True,
        "linger": False,
    }
    schedule.SYSTEMD_UNITS.mkdir(parents=True)
    schedule.SPOOL_SYNC_SERVICE.write_text("service")
    schedule.SPOOL_SYNC_TIMER.write_text("timer")
    (home / "linger").mkdir()
    (home / "linger" / getpass.getuser()).write_text("")
    status = schedule.spool_sync_status()
    assert (status["installed"], status["linger"]) == (True, True)
    assert systemd.calls == [("is-enabled", timer), ("is-active", timer)] * 2

    monkeypatch.setattr(sys, "platform", "darwin")
    assert schedule.spool_sync_status() == {"scheduler": None, "platform": "darwin"}

from __future__ import annotations

import plistlib
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from agent_introspection import dashboard_service, schedule
from agent_introspection.config import (
    AppConfig,
    ConfigurationError,
    MachineConfig,
    MachineRole,
    SigNozConfig,
)

REPO = Path(__file__).parents[1]
BUN = Path("/opt/bun/bin/bun")


class Recorder:
    """Records bun and scheduler commands, answering each with ``code``."""

    def __init__(self, code: int = 0) -> None:
        self.code = code
        self.bun: list[tuple[tuple[str, ...], Path]] = []
        self.scheduler: list[tuple[str, ...]] = []

    def run_bun(self, command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        self.bun.append((tuple(command), cwd))
        return subprocess.CompletedProcess(list(command), self.code, "", "bun: lockfile changed")

    def control(self, *args: str) -> subprocess.CompletedProcess[str]:
        self.scheduler.append(args)
        return subprocess.CompletedProcess(list(args), 0, "", "")


@pytest.fixture
def recorder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Recorder:
    recorded = Recorder()
    monkeypatch.setattr(dashboard_service, "_run_bun", recorded.run_bun)
    monkeypatch.setattr(dashboard_service, "_bun", lambda: BUN)
    monkeypatch.setattr(dashboard_service, "PLIST", tmp_path / "agents/dashboard.plist")
    monkeypatch.setattr(dashboard_service, "SERVICE", tmp_path / "units/dashboard.service")
    monkeypatch.setattr(schedule, "launchctl", recorded.control)
    monkeypatch.setattr(schedule, "systemctl", recorded.control)
    monkeypatch.setattr(schedule, "launchd_loaded", lambda _label: False)
    monkeypatch.setattr(schedule, "launchd_domain", lambda: "gui/501")
    return recorded


def test_install_builds_in_the_checkout_then_starts_a_kept_alive_launchd_agent(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(schedule, "platform_scheduler", lambda: "launchd")
    config = Path("/etc/ai.toml")

    result = dashboard_service.install(AppConfig(), REPO, config)

    directory = REPO / "dashboard"
    assert recorder.bun == [
        ((str(BUN), "install", "--frozen-lockfile"), directory),
        ((str(BUN), "run", "build"), directory),
    ]
    agent = plistlib.loads(dashboard_service.PLIST.read_bytes())
    assert agent["ProgramArguments"] == [str(BUN), "server.ts"]
    assert agent["WorkingDirectory"] == str(directory)
    assert agent["KeepAlive"] is True
    assert agent["EnvironmentVariables"] == {
        "PATH": "/opt/bun/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "AGENT_INTROSPECTION_CONFIG": str(config),
    }
    assert recorder.scheduler == [("bootstrap", "gui/501", str(dashboard_service.PLIST))]
    assert result == {
        "installed": True,
        "scheduler": "launchd",
        "directory": str(directory),
        "url": "http://127.0.0.1:4173",
    }


def test_install_restarts_a_systemd_user_service(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(schedule, "platform_scheduler", lambda: "systemd")

    dashboard_service.install(AppConfig(), REPO)

    unit = dashboard_service.SERVICE.read_text()
    assert f'WorkingDirectory="{REPO / "dashboard"}"\n' in unit
    assert f'ExecStart="{BUN}" server.ts\n' in unit
    assert "Restart=on-failure\n" in unit
    assert "AGENT_INTROSPECTION_CONFIG" not in unit
    name = dashboard_service.SERVICE.name
    assert recorder.scheduler == [("daemon-reload",), ("enable", name), ("restart", name)]


def test_a_failed_build_stops_before_the_service(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(schedule, "platform_scheduler", lambda: "launchd")
    recorder.code = 1

    with pytest.raises(RuntimeError, match="bun install --frozen-lockfile failed: bun: lockfile"):
        dashboard_service.install(AppConfig(), REPO)

    assert recorder.scheduler == []
    assert not dashboard_service.PLIST.exists()


def test_install_refuses_a_path_that_is_not_a_checkout(
    recorder: Recorder, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(schedule, "platform_scheduler", lambda: "launchd")

    with pytest.raises(ValueError, match="is not an agent-introspection checkout"):
        dashboard_service.install(AppConfig(), tmp_path)
    assert recorder.bun == []


def test_install_refuses_a_password_and_an_additional_machine(recorder: Recorder) -> None:
    password = AppConfig(signoz=SigNozConfig(clickhouse_password_command=("security", "-w")))
    with pytest.raises(RuntimeError, match="cannot run a ClickHouse password command"):
        dashboard_service.install(password, REPO)
    additional = AppConfig(
        machine=MachineConfig(role=MachineRole.ADDITIONAL, export_endpoint="http://host:4318")
    )
    with pytest.raises(ConfigurationError, match="additional machine"):
        dashboard_service.install(additional, REPO)
    assert recorder.bun == []


def test_remove_stops_and_deletes_the_service(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(schedule, "platform_scheduler", lambda: "systemd")
    dashboard_service.SERVICE.parent.mkdir(parents=True)
    dashboard_service.SERVICE.write_text("[Unit]\n")

    assert dashboard_service.remove() == {"removed": True, "scheduler": "systemd"}

    assert not dashboard_service.SERVICE.exists()
    name = dashboard_service.SERVICE.name
    assert recorder.scheduler == [("disable", "--now", name), ("daemon-reload",)]

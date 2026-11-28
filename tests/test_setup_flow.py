from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agent_introspection import dashboard_service, facts, schedule, setup_flow
from agent_introspection.cli import EXIT_FACTS, main
from agent_introspection.config import AppConfig, load_config
from agent_introspection.setup_flow import SetupOptions
from tests.support.host_probes import fake_network, fake_systemd, fake_version_probe
from tests.support.preflight_clickhouse import fake_clickhouse

REPO = Path(__file__).parents[1]
_INSTALLS = ("CREATE ", "DROP ", "INSERT ", "SYSTEM ", "ALTER ", "EXCHANGE ", "RENAME ")


class Host:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self.connected: list[str] = []
        self.statements: list[str] = []
        self.bun: list[list[str]] = []
        self.preflight_passed = True
        executable = tmp_path / "venv/bin/python"
        executable.parent.mkdir(parents=True)
        executable.with_name("agent-introspection").touch()
        monkeypatch.setattr(facts, "runner", self.runner)
        monkeypatch.setattr(schedule, "_python_executable", lambda: str(executable))
        monkeypatch.setattr(schedule, "_which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(dashboard_service, "_bun", lambda: Path("/fake/bin/bun"))
        monkeypatch.setattr(dashboard_service, "_run_bun", self.run_bun)
        self.systemctl = fake_systemd(monkeypatch)
        fake_network(monkeypatch)
        fake_version_probe(monkeypatch)

    def runner(self, config: AppConfig) -> facts.SqlRunner:
        self.connected.append(config.signoz.clickhouse_url)
        version = "25.5.6.14" if self.preflight_passed else "23.8.1.1"
        _executed, preflight = fake_clickhouse(version=version, grants={})

        def run(sql: str) -> str:
            self.statements.append(sql)
            if sql.startswith("SELECT count() FROM introspection.") and sql.endswith(" FINAL"):
                return "0\n"
            if "system.databases WHERE name LIKE" in sql or "rows_24h" in sql:
                return ""
            answer: str = preflight(sql)
            return answer

        return run

    def run_bun(self, command: Sequence[str], _cwd: Path) -> subprocess.CompletedProcess[str]:
        self.bun.append(list(command))
        return subprocess.CompletedProcess(list(command), 0, "", "")

    def installed(self) -> list[str]:
        return [sql for sql in self.statements if sql.lstrip().startswith(_INSTALLS)]


@pytest.fixture
def host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Host:
    return Host(monkeypatch, tmp_path)


def configured(result: dict[str, Any]) -> list[tuple[str, bool, bool]]:
    return [
        (key, item["dry_run"], item["prompt_export"])
        for key, item in result["steps"]["harnesses"].items()
    ]


def options(tmp_path: Path, **overrides: Any) -> SetupOptions:
    return SetupOptions(config_path=tmp_path / "config.toml", repo=REPO, **overrides)


def test_setup_writes_only_the_given_settings_and_runs_every_step_in_order(
    host: Host, tmp_path: Path
) -> None:
    result = setup_flow.run(
        options(
            tmp_path,
            harnesses=("claude-code", "codex", "oh-my-pi"),
            openrouter_key_command=("security", "-w"),
            prompt_export=True,
            backfill_days=30,
        )
    )

    assert result["passed"] is True, result.get("doctor", result)
    config = load_config(tmp_path / "config.toml")
    assert config.harnesses_enabled == ("claude-code", "codex", "oh-my-pi")
    assert config.labels_openrouter_key_command == ("security", "-w")
    assert config == load_config(tmp_path / "config.toml")
    assert "[signoz]" not in (tmp_path / "config.toml").read_text()
    assert list(result["steps"]) == [
        "config",
        "signoz",
        "facts",
        "harnesses",
        "schedule",
        "dashboard",
    ]
    assert result["doctor"]["passed"] is True
    assert configured(result) == [
        ("claude-code", False, True),
        ("codex", False, True),
        ("omp", False, True),
    ]
    backfill = result["steps"]["facts"]["backfill"]
    window = datetime.fromisoformat(backfill["end"]) - datetime.fromisoformat(backfill["start"])
    assert timedelta(days=30) <= window < timedelta(days=31)
    assert result["steps"]["schedule"]["installed"] is True
    assert result["steps"]["dashboard"]["installed"] is True
    assert result["steps"]["config"]["action"] == "add"


def test_a_dashboard_port_is_written_to_the_config_and_served(host: Host, tmp_path: Path) -> None:
    result = setup_flow.run(options(tmp_path, dashboard_port=5174))

    assert result["passed"] is True, result.get("doctor", result)
    assert load_config(tmp_path / "config.toml").dashboard.port == 5174
    assert result["steps"]["dashboard"]["url"] == "http://127.0.0.1:5174"


def test_a_dry_run_changes_nothing_and_previews_each_harness(host: Host, tmp_path: Path) -> None:
    result = setup_flow.run(
        options(tmp_path, dry_run=True, clickhouse_url="http://localhost:18123")
    )

    assert not (tmp_path / "config.toml").exists()
    assert '+clickhouse_url = "http://localhost:18123"' in result["steps"]["config"]["diff"]
    assert host.connected == ["http://localhost:18123"]
    assert (host.installed(), host.systemctl, host.bun) == ([], [], [])
    assert list(Path.home().iterdir()) == []
    assert result["passed"] is True
    assert "doctor" not in result
    assert configured(result) == [
        ("claude-code", True, False),
        ("codex", True, False),
    ]
    assert result["steps"]["facts"]["planned"][0] == "facts install"
    assert result["steps"]["dashboard"]["planned"].startswith("dashboard install --repo ")


def test_a_failing_preflight_stops_before_anything_is_installed(host: Host, tmp_path: Path) -> None:
    host.preflight_passed = False

    result = setup_flow.run(options(tmp_path))

    assert (result["passed"], result["stopped_at"]) == (False, "signoz")
    assert result["error"].startswith("clickhouse_version: ClickHouse 23.8.1.1; ")
    assert result["error"].endswith("need 24.10 or later")
    assert len(host.connected) == 1
    assert (host.installed(), host.systemctl, host.bun) == ([], [], [])
    assert list(result["steps"]) == ["config"]


def test_an_existing_config_keeps_its_comments_and_gets_a_backup(
    host: Host, tmp_path: Path
) -> None:
    path = tmp_path / "config.toml"
    path.write_text('# mine\n[harnesses]\nenabled = ["claude-code"]  # just Claude\n')
    path.chmod(0o640)

    setup_flow.run(options(tmp_path, harnesses=("claude-code", "codex")))

    text = path.read_text()
    assert text.startswith("# mine\n")
    assert load_config(path).harnesses_enabled == ("claude-code", "codex")
    assert path.stat().st_mode & 0o777 == 0o640
    assert [backup.read_text() for backup in tmp_path.glob("config.toml.bak-*")] == [
        '# mine\n[harnesses]\nenabled = ["claude-code"]  # just Claude\n'
    ]


def test_an_additional_machine_stops_at_the_config(host: Host, tmp_path: Path) -> None:
    existing = '[machine]\nrole = "additional"\nexport_endpoint = "http://host.local:4318"\n'
    (tmp_path / "config.toml").write_text(existing)

    result = setup_flow.run(options(tmp_path))

    assert result["stopped_at"] == "config"
    assert 'which this additional machine ([machine] role = "additional")' in result["error"]
    assert (host.connected, host.statements) == ([], [])
    assert (tmp_path / "config.toml").read_text() == existing


OLD_CONFIG = """# mine
[signoz]
docker_context = "orbstack"  # retired
clickhouse_container = "ch"

[sessions.roots]
codex = ["~/x"]

[dashboard]
clickhouse_url = "http://clickhouse.orb.local:8123"

[harnesses]
enabled = ["claude-code"]
"""


def test_an_upgrade_removes_retired_settings_and_moves_the_dashboard_url(
    host: Host, tmp_path: Path
) -> None:
    (tmp_path / "config.toml").write_text(OLD_CONFIG)

    dry = setup_flow.run(options(tmp_path, dry_run=True))
    assert dry["passed"] is True
    assert (tmp_path / "config.toml").read_text() == OLD_CONFIG
    result = setup_flow.run(options(tmp_path))

    report = result["steps"]["config"]
    assert report["retired_removed"] == [
        "sessions",
        "dashboard.clickhouse_url",
        "signoz.docker_context",
        "signoz.clickhouse_container",
    ]
    assert report["notes"] == ["[dashboard] clickhouse_url moved to [signoz] clickhouse_url"]
    assert report["backup"] is not None
    config = load_config(tmp_path / "config.toml")
    assert config.signoz.clickhouse_url == "http://clickhouse.orb.local:8123"
    assert config.harnesses_enabled == ("claude-code",)
    assert "# mine" in (tmp_path / "config.toml").read_text()


@pytest.mark.parametrize("plugin", [True, False], ids=["omp-plugin", "no-plugin"])
def test_an_older_config_keeps_omp_while_its_plugin_is_installed(
    host: Host, tmp_path: Path, plugin: bool
) -> None:
    (tmp_path / "config.toml").write_text("[database]\n")
    if plugin:
        link = Path.home() / ".omp/plugins/node_modules/agent-introspection-activity"
        link.parent.mkdir(parents=True)
        link.symlink_to(tmp_path)

    result = setup_flow.run(options(tmp_path))

    expected = ("claude-code", "codex", "oh-my-pi") if plugin else ("claude-code", "codex")
    assert load_config(tmp_path / "config.toml").harnesses_enabled == expected
    assert bool(result["steps"]["config"]["notes"]) is plugin


def test_a_new_config_path_is_written_even_with_nothing_to_set(host: Host, tmp_path: Path) -> None:
    result = setup_flow.run(options(tmp_path))

    assert result["passed"] is True
    assert (tmp_path / "config.toml").exists()
    assert result["steps"]["config"]["action"] == "add"


@pytest.mark.parametrize("dry_run", [True, False])
def test_a_clickhouse_password_skips_the_dashboard_service(
    host: Host, tmp_path: Path, dry_run: bool
) -> None:
    result = setup_flow.run(
        options(tmp_path, dry_run=dry_run, clickhouse_password_command=("sh", "-c", "echo pw"))
    )

    assert result["steps"]["dashboard"]["skipped"].startswith("a ClickHouse password")
    assert host.bun == []


def test_no_dashboard_skips_the_service(host: Host, tmp_path: Path) -> None:
    result = setup_flow.run(options(tmp_path, dashboard=False))

    assert result["steps"]["dashboard"] == {"skipped": "--no-dashboard"}
    assert host.bun == []


def test_each_step_is_announced_as_it_starts_and_ends(host: Host, tmp_path: Path) -> None:
    lines: list[str] = []

    setup_flow.run(options(tmp_path), progress=lines.append)

    started = [line.split(":")[1].strip() for line in lines if line.endswith("...")]
    assert started == [
        "config",
        "signoz",
        "facts",
        "harnesses",
        "schedule",
        "dashboard",
        "doctor",
    ]
    assert "a long backfill can take minutes" in lines[4]
    ended = next(index for index, line in enumerate(lines) if line.startswith("setup: facts: done"))
    relayed = lines[5:ended]
    assert relayed
    assert all(line.startswith("facts backfill: ") for line in relayed)
    assert relayed[0].startswith("facts backfill: spans (table 1 of 3): ")
    assert relayed[-1].endswith("(table 3 of 3): 100%")
    assert len(lines) == 2 * len(started) + len(relayed)
    assert lines[1].startswith("setup: config: done in ")


def test_a_failing_step_is_announced_as_failed(host: Host, tmp_path: Path) -> None:
    host.preflight_passed = False
    lines: list[str] = []

    setup_flow.run(options(tmp_path, dry_run=True), progress=lines.append)

    assert lines[-1].startswith("setup: signoz: failed in ")
    assert not any("backfill" in line for line in lines)


def test_the_command_passes_its_options_and_exits_non_zero_when_a_step_fails(
    host: Host, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    argv = [
        "--config",
        str(config),
        "setup",
        "--harness",
        "claude-code",
        "--openrouter-key-command",
        "security find-generic-password -s openrouter -w",
        "--no-dashboard",
    ]

    assert main(argv) == 0, capsys.readouterr().out
    printed = capsys.readouterr()
    assert json.loads(printed.out)["passed"] is True
    assert "setup: doctor: done in " in printed.err
    assert load_config(config).labels_openrouter_key_command == (
        "security",
        "find-generic-password",
        "-s",
        "openrouter",
        "-w",
    )

    host.preflight_passed = False
    assert main(argv) == EXIT_FACTS
    assert json.loads(capsys.readouterr().out)["stopped_at"] == "signoz"


def test_an_unknown_harness_key_stops_both_runs_at_the_config(host: Host, tmp_path: Path) -> None:
    for dry_run in (True, False):
        result = setup_flow.run(options(tmp_path, harnesses=("omp",), dry_run=dry_run))

        assert (result["stopped_at"], result["passed"]) == ("config", False)
        assert "unknown harness omp" in result["error"]
    assert not (tmp_path / "config.toml").exists()
    assert (host.connected, host.statements) == ([], [])


def test_a_zero_dashboard_port_stops_at_the_config_instead_of_using_the_default(
    host: Host, tmp_path: Path
) -> None:
    result = setup_flow.run(options(tmp_path, dashboard_port=0))

    assert (result["stopped_at"], result["passed"]) == ("config", False)
    assert "TCP port" in result["error"]
    assert not (tmp_path / "config.toml").exists()


def test_a_symlinked_config_is_written_through(host: Host, tmp_path: Path) -> None:
    target = tmp_path / "dotfiles/config.toml"
    target.parent.mkdir()
    target.write_text("# kept\n")
    link = tmp_path / "config.toml"
    link.symlink_to(target)

    setup_flow.run(options(tmp_path, backfill_days=1))

    assert link.is_symlink()
    assert "# kept" in target.read_text()

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_introspection import setup_flow
from agent_introspection.cli import EXIT_FACTS, main
from agent_introspection.config import AppConfig, load_config
from agent_introspection.setup_flow import SetupOptions

REPO = Path(__file__).parents[1]


class Steps:
    """Fakes every step's effect and records the calls, in order."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.preflight_passed = True
        package = "agent_introspection"
        for target, fake in (
            ("preflight.report", self.preflight),
            ("facts.runner", lambda _config: self.sql),
            ("facts.install", self.install),
            ("facts.backfill", self.backfill),
            ("harness_config.configure", self.configure),
            ("schedule.schedule_install", self.schedule),
            ("dashboard_service.install", self.dashboard),
            ("cli_doctor.default_probes", lambda: "probes"),
            ("doctor.report", self.doctor),
            ("setup_flow.connect_workflow", lambda *_args, **_kwargs: _Closable()),
        ):
            monkeypatch.setattr(f"{package}.{target}", fake)

    def sql(self, _statement: str) -> str:
        return ""

    def preflight(self, load: Any, _connect: Any) -> dict[str, Any]:
        self.calls.append(("preflight", load().signoz.clickhouse_url))
        checks = [{"name": "clickhouse_version", "status": "fail", "detail": "23.8 < 24.10"}]
        return {"passed": self.preflight_passed, "checks": [] if self.preflight_passed else checks}

    def install(self, _run: Any, enabled: Any) -> dict[str, Any]:
        self.calls.append(("facts install", tuple(enabled)))
        return {"enabled": list(enabled)}

    def backfill(self, _run: Any, *, days: int, progress: Any = None) -> dict[str, Any]:
        self.calls.append(("backfill", days))
        return {"rows": {}}

    def configure(self, key: str, endpoint: Any, options: Any) -> dict[str, Any]:
        self.calls.append(("configure", (key, options.dry_run, options.prompt_export)))
        return {"harness": key}

    def schedule(self, config: Any) -> dict[str, Any]:
        self.calls.append(("schedule", config))
        return {"installed": True}

    def dashboard(self, _config: AppConfig, repo: Path, config: Any) -> dict[str, Any]:
        self.calls.append(("dashboard", repo))
        return {"installed": True}

    def doctor(self, options: Any, probes: Any) -> dict[str, Any]:
        self.calls.append(("doctor", probes))
        return {"passed": True, "summary": {"pass": 1}, "checks": []}

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


@pytest.fixture
def steps(monkeypatch: pytest.MonkeyPatch) -> Steps:
    return Steps(monkeypatch)


class _Closable:
    def close(self) -> None:
        pass


def options(tmp_path: Path, **overrides: Any) -> SetupOptions:
    return SetupOptions(config_path=tmp_path / "config.toml", repo=REPO, **overrides)


def test_setup_writes_only_the_given_settings_and_runs_every_step_in_order(
    steps: Steps, tmp_path: Path
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

    assert result["passed"] is True
    config = load_config(tmp_path / "config.toml")
    assert config.harnesses_enabled == ("claude-code", "codex", "oh-my-pi")
    assert config.labels_openrouter_key_command == ("security", "-w")
    assert config == load_config(tmp_path / "config.toml")
    assert "[signoz]" not in (tmp_path / "config.toml").read_text()
    assert steps.names() == [
        "preflight",
        "facts install",
        "backfill",
        "configure",
        "configure",
        "configure",
        "schedule",
        "dashboard",
        "doctor",
    ]
    configured = [value for name, value in steps.calls if name == "configure"]
    assert configured == [
        ("claude-code", False, True),
        ("codex", False, True),
        ("omp", False, True),
    ]
    assert ("backfill", 30) in steps.calls
    assert result["steps"]["config"]["action"] == "add"


def test_a_dry_run_changes_nothing_and_previews_each_harness(steps: Steps, tmp_path: Path) -> None:
    result = setup_flow.run(
        options(tmp_path, dry_run=True, clickhouse_url="http://localhost:18123")
    )

    assert not (tmp_path / "config.toml").exists()
    assert '+clickhouse_url = "http://localhost:18123"' in result["steps"]["config"]["diff"]
    assert steps.names() == ["preflight", "configure", "configure"]
    assert result["passed"] is True
    assert "doctor" not in result
    assert ("preflight", "http://localhost:18123") in steps.calls
    assert [value for name, value in steps.calls if name == "configure"] == [
        ("claude-code", True, False),
        ("codex", True, False),
    ]
    assert result["steps"]["facts"]["planned"][0] == "facts install"
    assert result["steps"]["dashboard"]["planned"].startswith("dashboard install --repo ")


def test_a_failing_preflight_stops_before_anything_is_installed(
    steps: Steps, tmp_path: Path
) -> None:
    steps.preflight_passed = False

    result = setup_flow.run(options(tmp_path))

    assert (result["passed"], result["stopped_at"]) == (False, "signoz")
    assert result["error"] == "clickhouse_version: 23.8 < 24.10"
    assert steps.names() == ["preflight"]


def test_an_existing_config_keeps_its_comments_and_gets_a_backup(
    steps: Steps, tmp_path: Path
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


def test_an_additional_machine_stops_at_the_config(steps: Steps, tmp_path: Path) -> None:
    existing = '[machine]\nrole = "additional"\nexport_endpoint = "http://host.local:4318"\n'
    (tmp_path / "config.toml").write_text(existing)

    result = setup_flow.run(options(tmp_path))

    assert result["stopped_at"] == "config"
    assert 'which this additional machine ([machine] role = "additional")' in result["error"]
    assert steps.names() == []
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
    steps: Steps, tmp_path: Path
) -> None:
    (tmp_path / "config.toml").write_text(OLD_CONFIG)

    dry = setup_flow.run(options(tmp_path, dry_run=True))
    assert dry["passed"] is True
    assert (tmp_path / "config.toml").read_text() == OLD_CONFIG
    result = setup_flow.run(options(tmp_path))

    report = result["steps"]["config"]
    assert report["retired_removed"] == [
        "sessions",
        "dashboard",
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
    steps: Steps, tmp_path: Path, plugin: bool
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


def test_a_new_config_path_is_written_even_with_nothing_to_set(
    steps: Steps, tmp_path: Path
) -> None:
    result = setup_flow.run(options(tmp_path))

    assert result["passed"] is True
    assert (tmp_path / "config.toml").exists()
    assert result["steps"]["config"]["action"] == "add"


@pytest.mark.parametrize("dry_run", [True, False])
def test_a_clickhouse_password_skips_the_dashboard_service(
    steps: Steps, tmp_path: Path, dry_run: bool
) -> None:
    result = setup_flow.run(
        options(tmp_path, dry_run=dry_run, clickhouse_password_command=("sh", "-c", "echo pw"))
    )

    assert result["steps"]["dashboard"]["skipped"].startswith("a ClickHouse password")
    assert "dashboard" not in steps.names()


def test_no_dashboard_skips_the_service(steps: Steps, tmp_path: Path) -> None:
    result = setup_flow.run(options(tmp_path, dashboard=False))

    assert result["steps"]["dashboard"] == {"skipped": "--no-dashboard"}
    assert "dashboard" not in steps.names()


def test_each_step_is_announced_as_it_starts_and_ends(steps: Steps, tmp_path: Path) -> None:
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
    assert len(lines) == 2 * len(started)
    assert "a long backfill can take minutes" in lines[4]
    assert lines[1].startswith("setup: config: done in ")


def test_a_failing_step_is_announced_as_failed(steps: Steps, tmp_path: Path) -> None:
    steps.preflight_passed = False
    lines: list[str] = []

    setup_flow.run(options(tmp_path, dry_run=True), progress=lines.append)

    assert lines[-1].startswith("setup: signoz: failed in ")
    assert not any("backfill" in line for line in lines)


def test_the_command_passes_its_options_and_exits_non_zero_when_a_step_fails(
    steps: Steps, tmp_path: Path, capsys: pytest.CaptureFixture[str]
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

    assert main(argv) == 0
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

    steps.preflight_passed = False
    assert main(argv) == EXIT_FACTS
    assert json.loads(capsys.readouterr().out)["stopped_at"] == "signoz"


def test_an_unknown_harness_key_stops_both_runs_at_the_config(steps: Steps, tmp_path: Path) -> None:
    for dry_run in (True, False):
        result = setup_flow.run(options(tmp_path, harnesses=("omp",), dry_run=dry_run))

        assert (result["stopped_at"], result["passed"]) == ("config", False)
        assert "unknown harness omp" in result["error"]
    assert not (tmp_path / "config.toml").exists()
    assert steps.names() == []


def test_a_symlinked_config_is_written_through(steps: Steps, tmp_path: Path) -> None:
    target = tmp_path / "dotfiles/config.toml"
    target.parent.mkdir()
    target.write_text("# kept\n")
    link = tmp_path / "config.toml"
    link.symlink_to(target)

    setup_flow.run(options(tmp_path, backfill_days=1))

    assert link.is_symlink()
    assert "# kept" in target.read_text()

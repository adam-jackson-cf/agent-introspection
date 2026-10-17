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

    def backfill(self, _run: Any, *, days: int) -> dict[str, Any]:
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


@pytest.mark.parametrize(
    ("existing", "message"),
    [
        ('[sessions.roots]\ncodex = ["~/x"]\n', "sessions retired; delete them"),
        (
            '[machine]\nrole = "additional"\nexport_endpoint = "http://host.local:4318"\n',
            'which this additional machine ([machine] role = "additional") does not have',
        ),
    ],
)
def test_a_retired_key_or_an_additional_machine_stops_at_the_config(
    steps: Steps, tmp_path: Path, existing: str, message: str
) -> None:
    (tmp_path / "config.toml").write_text(existing)

    result = setup_flow.run(options(tmp_path))

    assert result["stopped_at"] == "config"
    assert message in result["error"]
    assert steps.names() == []
    assert (tmp_path / "config.toml").read_text() == existing


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
    assert json.loads(capsys.readouterr().out)["passed"] is True
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

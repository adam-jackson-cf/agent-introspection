import json
from pathlib import Path

import pytest

from agent_introspection import cli
from agent_introspection.cli import EXIT_CONFIG, EXIT_FACTS, EXIT_VALIDATION, main
from agent_introspection.facts import FactsError


def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(f'[database]\npath = "{tmp_path / "workflow.sqlite3"}"\n')
    return path


def test_cli_emits_structured_json_and_creates_the_workflow_store(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    assert main(["--config", str(config_file(tmp_path)), "proposal", "list"]) == 0

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"proposals": []}
    assert captured.err == ""
    assert (tmp_path / "workflow.sqlite3").exists()


def test_cli_emits_diagnostics_on_stderr_with_stable_exit_codes(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    retired = tmp_path / "retired.toml"
    retired.write_text("[scheduler]\ninterval_seconds = 300\n")
    with pytest.raises(SystemExit) as raised:
        main(["--config", str(retired), "proposal", "list"])
    assert raised.value.code == EXIT_CONFIG
    assert "ConfigurationError" in capsys.readouterr().err

    with pytest.raises(SystemExit) as raised:
        main(["--config", str(config_file(tmp_path)), "proposal", "show", "missing"])
    captured = capsys.readouterr()
    assert raised.value.code == EXIT_VALIDATION
    assert captured.out == ""
    assert "KeyError" in captured.err


def test_facts_failures_exit_with_the_facts_code(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def failing(_statement: str) -> str:
        raise FactsError("Code: 81. DB::Exception: Database introspection does not exist")

    monkeypatch.setattr(cli.facts, "docker_runner", lambda _config: failing)

    with pytest.raises(SystemExit) as raised:
        main(["--config", str(config_file(tmp_path)), "facts", "status"])

    assert raised.value.code == EXIT_FACTS
    assert "does not exist" in capsys.readouterr().err


def test_candidates_export_reports_no_candidates_without_actionable_findings(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    config = str(config_file(tmp_path))

    assert main(["--config", config, "candidates", "export", "--reserved-model-budget", "10"]) == 0

    assert json.loads(capsys.readouterr().out) == {"status": "no_candidates"}

import json
from pathlib import Path
from typing import Any

import pytest

from agent_introspection import cli_facts, evaluation, facts, findings, spool
from agent_introspection.cli import main
from tests.test_cli import config_file


def test_facts_sync_ships_the_spool_then_runs_the_loaders_before_labels_and_findings(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    steps: list[str] = []

    def run(statement: str) -> str:
        steps.append(statement)
        return ""

    def step(name: str, result: Any) -> Any:
        def call(*_args: object, **_kwargs: object) -> Any:
            steps.append(name)
            return result

        return call

    monkeypatch.setattr(facts, "runner", lambda _config: run)
    monkeypatch.setattr(spool, "sync", step("spool", {"drain": {"sent": 1}}))
    monkeypatch.setattr(cli_facts, "_labels", step("labels", {"status": "ok"}))
    monkeypatch.setattr(findings, "refresh", step("findings", {"promoted": 0}))
    monkeypatch.setattr(evaluation, "evaluate_due", step("evaluations", {"evaluated": 0}))

    assert main(["--config", str(config_file(tmp_path)), "facts", "sync"]) == 0

    assert steps == [
        "spool",
        "SYSTEM REFRESH VIEW introspection.load_hook_events",
        "SYSTEM WAIT VIEW introspection.load_hook_events",
        "SYSTEM REFRESH VIEW introspection.load_session_projects",
        "SYSTEM WAIT VIEW introspection.load_session_projects",
        "labels",
        "findings",
        "evaluations",
    ]
    assert json.loads(capsys.readouterr().out)["spool"] == {"drain": {"sent": 1}}

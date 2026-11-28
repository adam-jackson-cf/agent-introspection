import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_introspection import facts, schedule, spool
from agent_introspection.cli import main
from agent_introspection.facts import FactsError
from agent_introspection.findings import WINDOW_QUERY
from tests.support.cli_config import config_file
from tests.support.http_response import CannedResponse

SUCCESS_METRIC = {
    "metric": "cluster_task_rate",
    "harnesses": ["codex_exec"],
    "baseline_days": 14,
    "evaluation_days": 14,
    "max_ratio": 0.5,
}
SUBJECT = {"tool_family": "shell", "failure_class": "not_found", "harnesses": ["codex_exec"]}


def seed_applied_proposal(path: Path) -> None:
    applied = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO findings (
                id, fingerprint, category, trend_state, detector_id, detector_version,
                first_seen_ns, last_seen_ns, occurrence_count, canonical_task_count,
                local_day_count, entity_version, updated_at, subject
            ) VALUES ('finding-1', ?, 'tool_failure', 'actionable', 'tool_failure', 1,
                1, 2, 3, 2, 2, 1, 'now', ?)
            """,
            ("f" * 64, json.dumps(SUBJECT)),
        )
        connection.execute(
            "INSERT INTO proposals VALUES ('proposal-1', 'finding-1', 'applied', ?, ?, ?, 4)",
            (json.dumps({"predicted_success_metric": SUCCESS_METRIC}), applied, applied),
        )
        connection.execute(
            "INSERT INTO proposal_events VALUES ('event-1', 'proposal-1', 1, 'applied', '{}', ?)",
            (applied,),
        )
    connection.close()


def test_facts_sync_ships_the_spool_then_runs_the_loaders_before_labels_and_findings(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    steps: list[str] = []

    def run(statement: str) -> str:
        if "FROM signoz_logs.distributed_logs_v2" in statement:
            steps.append("labels")
        elif statement == WINDOW_QUERY:
            steps.append("findings")
        elif "AS matched_tasks" in statement:
            steps.append("evaluations")
        else:
            steps.append(statement)
        return ""

    def post(_request: object, _timeout: float) -> CannedResponse:
        steps.append("spool")
        return CannedResponse(b"{}")

    monkeypatch.setattr(facts, "runner", lambda _config: run)
    monkeypatch.setattr(spool, "_urlopen", post)
    config = config_file(tmp_path)
    config.write_text(config.read_text() + '[labels]\nopenrouter_key_command = ["security"]\n')
    assert main(["--config", str(config), "proposal", "list"]) == 0
    capsys.readouterr()
    seed_applied_proposal(tmp_path / "workflow.sqlite3")

    assert main(["--config", str(config), "facts", "sync"]) == 0

    assert steps == [
        "spool",
        "SYSTEM REFRESH VIEW introspection.load_hook_events",
        "SYSTEM WAIT VIEW introspection.load_hook_events",
        "labels",
        "findings",
        "evaluations",
        "evaluations",
    ]
    result = json.loads(capsys.readouterr().out)
    assert result["spool"]["drain"]["sent"] == 1
    assert result["evaluations"]["evaluated"][0]["verdict"] == "inconclusive"


def test_a_logged_sync_writes_its_summary_and_errors_to_the_timestamped_log(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = schedule.log_file()
    down: list[bool] = []

    def run(_sql: str) -> str:
        if down:
            raise FactsError("clickhouse is down")
        return ""

    monkeypatch.setattr(facts, "runner", lambda _config: run)
    monkeypatch.setattr(spool, "_urlopen", lambda _request, _timeout: CannedResponse(b"{}"))

    assert main(["--config", str(config_file(tmp_path)), "facts", "sync", "--log"]) == 0
    down.append(True)
    with pytest.raises(SystemExit):
        main(["--config", str(config_file(tmp_path)), "facts", "sync", "--log"])

    stamped = [line.split(" ", 1) for line in log.read_text().splitlines()]
    summary, error = (rest for _, rest in stamped)
    assert summary == json.dumps(json.loads(summary), sort_keys=True, separators=(",", ":"))
    assert set(json.loads(summary)) == {"spool", "labels", "findings", "evaluations"}
    assert error == "FactsError: clickhouse is down"
    assert all(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", stamp) for stamp, _ in stamped)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""

import json
import sys
from pathlib import Path

import pytest

from agent_introspection import facts, schedule
from agent_introspection.cli import main
from tests.support.otlp_collector import Collector, names, spool_records
from tests.test_cli import config_file


def test_spool_sync_sends_without_touching_clickhouse(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    inbox: Path,
    collector: Collector,
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(f'[hooks]\notlp_logs_endpoint = "{collector.url}"\n')
    monkeypatch.setattr(facts, "runner", lambda _config: pytest.fail("no ClickHouse"))
    spool_records(inbox, 2)

    assert main(["--config", str(config), "spool", "sync"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["drain"]["sent"] == 3
    assert result["status"]["pending"] == 0
    assert names(inbox) == set()
    assert len(collector.records) == 3


def test_spool_status_counts_pending_and_quarantined_records(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, inbox: Path
) -> None:
    spool_records(inbox, 2)
    (inbox / "rejected").mkdir()
    (inbox / "rejected" / "old.json").write_text("{}")

    assert main(["--config", str(config_file(tmp_path)), "spool", "status"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert (result["pending"], result["rejected"]) == (2, 1)


def test_schedule_status_counts_the_spool_and_its_rejections(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, inbox: Path
) -> None:
    spool_records(inbox, 3)
    (inbox / "rejected").mkdir()
    (inbox / "rejected" / "old.json").write_text("{}")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(schedule, "PLIST", tmp_path / "missing.plist")
    monkeypatch.setattr(schedule, "LEGACY_PLIST", tmp_path / "missing-legacy.plist")
    monkeypatch.setattr(schedule, "_launchctl", lambda *_args: type("R", (), {"returncode": 1})())

    status = schedule.schedule_status()

    assert (status["inbox_backlog"], status["inbox_rejected"]) == (3, 1)

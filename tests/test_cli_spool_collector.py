import json
from pathlib import Path

import pytest

from agent_introspection import facts
from agent_introspection.cli import main
from tests.support.otlp_collector import Collector, names, spool_records

pytestmark = pytest.mark.boundary


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

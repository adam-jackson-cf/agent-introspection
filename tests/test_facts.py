from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from typing import Any

import pytest

from agent_introspection import facts
from agent_introspection.config import AppConfig
from agent_introspection.source import SourceError


def test_every_loader_renders_its_window_into_an_append_refresh() -> None:
    for loader in facts.LOADERS:
        ddl = facts.loader_ddl(loader)

        assert ddl.startswith(f"CREATE MATERIALIZED VIEW introspection.{loader.name}")
        assert f"REFRESH {loader.schedule} APPEND TO introspection.{loader.target}" in ddl
        assert loader.window in ddl
        assert "{window}" not in ddl


def test_span_loaders_select_by_end_time_and_log_loader_by_ingest_time() -> None:
    windows = {loader.name: loader.window for loader in facts.LOADERS}

    assert "toIntervalNanosecond(duration_nano)" in windows["load_spans"]
    assert windows["load_logs"].startswith("observed_timestamp")


def test_install_creates_tables_before_loaders_and_views() -> None:
    statements = facts.install_statements()

    assert "CREATE TABLE IF NOT EXISTS introspection.spans" in statements[0]
    drops = [s for s in statements if s.startswith("DROP VIEW")]
    creates = [s for s in statements if "MATERIALIZED VIEW" in s]
    assert len(drops) == len(creates) == len(facts.LOADERS)
    assert statements.index(drops[-1]) < statements.index(creates[0])
    assert "CREATE OR REPLACE VIEW introspection.usage_events" in statements[-1]


def test_backfill_splits_the_range_into_utc_days_ending_at_now() -> None:
    start = datetime(2026, 9, 1, tzinfo=UTC)
    end = datetime(2026, 9, 3, 12, 30, tzinfo=UTC)

    statements = facts.backfill_statements("logs", start, end)

    assert len(statements) == 3
    assert all(s.startswith("INSERT INTO introspection.logs") for s in statements)
    assert "'2026-09-01 00:00:00'" in statements[0]
    assert "'2026-09-03 12:30:00'" in statements[-1]


@pytest.mark.parametrize("key", ["'prompt'", "'arguments'", "'output'", "'user.email'"])
def test_log_projection_drops_raw_text_and_identity_keys(key: str) -> None:
    select = facts.render_select("select_logs.sql", "1")
    filtered_keys = select.split("mapFilter")[1].split("attributes_string")[0]

    assert key in filtered_keys


def test_span_projection_drops_prompt_text() -> None:
    select = facts.render_select("select_spans.sql", "1")
    filtered_keys = select.split("mapFilter")[1].split("attributes_string")[0]

    assert "'user_prompt'" in filtered_keys
    assert "'gen_ai.tool.description'" in filtered_keys


def test_docker_runner_sends_sql_on_stdin_and_surfaces_the_last_error_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(argv: tuple[str, ...], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"argv": argv, **kwargs})
        return subprocess.CompletedProcess(argv, 1, "", "trace\nCode: 62. DB::Exception: bad")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(SourceError, match="Code: 62"):
        facts.docker_runner(AppConfig())("SELECT 1")

    assert calls[0]["input"] == "SELECT 1"
    assert calls[0]["argv"][-2:] == ("clickhouse-client", "--multiquery")


def test_backfill_reports_final_row_counts() -> None:
    executed: list[str] = []

    def run(sql: str) -> str:
        executed.append(sql)
        return "7\n" if sql.startswith("SELECT count()") else ""

    result = facts.backfill(run, days=1, now=datetime(2026, 9, 28, 6, tzinfo=UTC))

    assert result["rows"] == {"spans": 7, "logs": 7}
    assert result["start"] == "2026-09-27T00:00:00+00:00"
    assert sum(sql.startswith("INSERT INTO") for sql in executed) == 4

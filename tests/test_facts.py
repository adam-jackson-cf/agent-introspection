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


def test_install_orders_tables_loaders_views_snapshots_then_registry_rows() -> None:
    statements = facts.install_statements()

    def first(fragment: str) -> int:
        return next(i for i, s in enumerate(statements) if fragment in s)

    assert "CREATE TABLE IF NOT EXISTS introspection.spans" in statements[0]
    assert "CREATE OR REPLACE TABLE introspection.signal_support" in statements[1]
    loaders = [s for s in statements if "APPEND TO" in s]
    assert len(loaders) == len(facts.LOADERS)
    views = first("CREATE OR REPLACE VIEW introspection.usage_events")
    assert first("APPEND TO") < views < first("CREATE MATERIALIZED VIEW introspection.refresh_")
    # Snapshot refreshers are dropped before loaders because they depend on them.
    assert first("DROP VIEW IF EXISTS introspection.refresh_") < first(
        "DROP VIEW IF EXISTS introspection.load_spans"
    )
    assert statements[-1].startswith("INSERT INTO introspection.signal_strays")


def test_every_fact_view_has_a_snapshot_refreshed_after_both_minute_loaders() -> None:
    for snapshot in facts.SNAPSHOTS:
        create_table, refresher = facts.snapshot_statements(snapshot)[2:]

        assert f"introspection.{snapshot.view}_snapshot" in create_table
        assert "EMPTY AS SELECT" in create_table
        assert "DEPENDS ON introspection.load_spans, introspection.load_logs" in refresher
        assert "APPEND" not in refresher
    views = facts._sql("003_views.sql")
    assert {s.view for s in facts.SNAPSHOTS} == {
        name.split(" ")[0] for name in views.split("CREATE OR REPLACE VIEW introspection.")[1:]
    }


def _document(**support: dict[str, str]) -> dict[str, Any]:
    harnesses = {
        h: h for h in ("oh-my-pi", "codex-app-server", "codex_cli_rs", "codex_exec", "claude-code")
    }
    return {
        "harnesses": harnesses,
        "views": {"cache": "Cache efficiency"},
        "route": [
            {
                "id": "omp.chat",
                "harnesses": ["oh-my-pi"],
                "source": "spans",
                "match": "1",
                "description": "d",
            },
            {
                "id": "codex.usage",
                "harnesses": ["codex-app-server", "codex_cli_rs", "codex_exec"],
                "source": "logs",
                "match": "1",
                "description": "d",
            },
        ],
        "signal": [
            {
                "id": "s",
                "view": "cache",
                "title": "t",
                "question": "q",
                "unit": "u",
                "formula": "f",
                "support": support,
            }
        ],
    }


def test_registry_expands_the_codex_group_and_lets_a_surface_override_it() -> None:
    registry = facts.parse_registry(
        _document(
            codex={"route": "codex.usage", "alignment": "aligned"},
            codex_exec={"alignment": "not emitted", "note": "headless"},
            **{
                "oh-my-pi": {"route": "omp.chat", "alignment": "aligned"},
                "claude-code": {"alignment": "not emitted", "note": "no field"},
            },
        )
    )

    by_harness = {row["harness"]: row for row in registry.support}
    assert by_harness["codex_cli_rs"]["route"] == "codex.usage"
    assert by_harness["codex_exec"] == {
        **by_harness["codex_exec"],
        "route": "",
        "alignment": "not emitted",
    }
    assert len(registry.routes) == 4


@pytest.mark.parametrize(
    ("support", "message"),
    [
        ({"codex": {"route": "codex.usage", "alignment": "aligned"}}, "every harness"),
        (
            {
                "codex": {"route": "omp.chat", "alignment": "aligned"},
                "oh-my-pi": {"route": "omp.chat", "alignment": "aligned"},
                "claude-code": {"alignment": "not emitted", "note": "n"},
            },
            "bad route",
        ),
        (
            {
                "codex": {"route": "codex.usage", "alignment": "differs"},
                "oh-my-pi": {"route": "omp.chat", "alignment": "aligned"},
                "claude-code": {"alignment": "not emitted", "note": "n"},
            },
            "needs a note",
        ),
        (
            {
                "codex": {"route": "codex.usage", "alignment": "aligned"},
                "oh-my-pi": {"route": "omp.chat", "alignment": "aligned"},
                "claude-code": {"alignment": "not emitted"},
            },
            "needs a note",
        ),
    ],
)
def test_registry_rejects_incomplete_or_undisclosed_support(
    support: dict[str, dict[str, str]], message: str
) -> None:
    with pytest.raises(facts.RegistryError, match=message):
        facts.parse_registry(_document(**support))


def test_repo_registry_gives_every_harness_signal_a_record_for_every_harness() -> None:
    registry = facts.load_registry()

    harness_signals = [s["signal"] for s in registry.signals if s["scope"] == "harness"]
    assert len(registry.support) == 5 * len(harness_signals)
    views = {s["view"] for s in registry.signals}
    assert views == set(facts.tomllib.loads(facts._sql(facts.REGISTRY_FILE))["views"])


def test_registry_rows_are_inserted_as_escaped_json_literals() -> None:
    registry = facts.Registry(
        signals=[], routes=[], support=[], strays=[{"stray": "x", "match": "name = 'a\\b'"}]
    )

    (statement,) = facts.registry_statements(registry)

    assert statement.startswith(
        "INSERT INTO introspection.signal_strays SELECT * FROM format(JSONEachRow, '"
    )
    assert "\\'a" in statement


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


def test_span_projection_drops_prompt_and_intent_text_and_normalizes_status() -> None:
    select = facts.render_select("select_spans.sql", "1")
    filtered_keys = select.split("mapFilter")[1].split("attributes_string")[0]

    assert "'user_prompt'" in filtered_keys
    assert "'gen_ai.tool.description'" in filtered_keys
    assert "'pi.gen_ai.tool.call.intent'" in filtered_keys
    status = select.split("AS status_message")[0].rsplit("substring(", 1)[1]
    assert "'~'" in status
    assert "'N'" in status
    assert "160" in status


def test_span_projection_keeps_only_the_codex_spans_the_facts_use() -> None:
    select = facts.render_select("select_spans.sql", "1")
    allowlist = select.split("serviceName NOT LIKE 'codex%'")[1].split("AND (1)")[0]
    views = facts._sql("003_views.sql")

    for name in ("session_task.turn", "run_sampling_request", "turn/interrupt", "turn/steer"):
        assert f"'{name}'" in allowlist
        assert f"'{name}'" in views


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

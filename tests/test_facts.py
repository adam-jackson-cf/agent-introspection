from __future__ import annotations

import re
import subprocess
import tomllib
from datetime import UTC, datetime
from typing import Any

import pytest

from agent_introspection import facts
from agent_introspection.config import AppConfig
from agent_introspection.facts import FactsError


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
    assert statements[-1].startswith("INSERT INTO introspection.signal_exclusions")


def test_every_fact_view_has_a_snapshot_refreshed_after_both_minute_loaders() -> None:
    for snapshot in facts.SNAPSHOTS:
        create_table, refresher = facts.snapshot_statements(snapshot)[2:]

        assert f"introspection.{snapshot.view}_snapshot" in create_table
        assert "EMPTY AS SELECT" in create_table
        assert "DEPENDS ON introspection.load_spans, introspection.load_logs" in refresher
        assert "APPEND" not in refresher
    views = facts._sql("003_views.sql")
    fact_views = {
        name.split(" ")[0] for name in views.split("CREATE OR REPLACE VIEW introspection.")[1:]
    }
    # session_project is a small lookup joined at query time, not a fact view.
    # Lookup views (latest project, hook tool details, hook harness) are not snapshotted.
    helpers = {"session_project", "hook_rows", "hook_tool_calls"}
    assert {s.view for s in facts.SNAPSHOTS} == fact_views - helpers


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
            {
                "id": "claude.usage",
                "harnesses": ["claude-code"],
                "source": "hooks",
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
            codex_exec={"alignment": "not applicable", "note": "headless"},
            **{
                "oh-my-pi": {"route": "omp.chat", "alignment": "aligned"},
                "claude-code": {"route": "claude.usage", "alignment": "differs", "note": "n"},
            },
        )
    )

    by_harness = {row["harness"]: row for row in registry.support}
    assert by_harness["codex_cli_rs"]["route"] == "codex.usage"
    assert by_harness["codex_exec"] == {
        **by_harness["codex_exec"],
        "route": "",
        "alignment": "not applicable",
    }
    assert len(registry.routes) == 5


@pytest.mark.parametrize(
    ("support", "message"),
    [
        ({"codex": {"route": "codex.usage", "alignment": "aligned"}}, "every harness"),
        (
            {
                "codex": {"route": "omp.chat", "alignment": "aligned"},
                "oh-my-pi": {"route": "omp.chat", "alignment": "aligned"},
                "claude-code": {"route": "claude.usage", "alignment": "aligned"},
            },
            "bad route",
        ),
        (
            {
                "codex": {"route": "codex.usage", "alignment": "differs"},
                "oh-my-pi": {"route": "omp.chat", "alignment": "aligned"},
                "claude-code": {"route": "claude.usage", "alignment": "aligned"},
            },
            "needs a note",
        ),
        (
            {
                "codex": {"route": "codex.usage", "alignment": "aligned"},
                "oh-my-pi": {"route": "omp.chat", "alignment": "aligned"},
                "claude-code": {"alignment": "not applicable"},
            },
            "needs a note",
        ),
        (
            {
                "codex": {"route": "codex.usage", "alignment": "aligned"},
                "oh-my-pi": {"route": "omp.chat", "alignment": "aligned"},
                "claude-code": {"alignment": "not emitted", "note": "no field"},
            },
            "breaks harness parity",
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
    assert views == set(tomllib.loads(facts._sql(facts.REGISTRY_FILE))["views"])


def test_registry_rows_are_inserted_as_escaped_json_literals() -> None:
    registry = facts.Registry(
        signals=[],
        routes=[],
        support=[],
        strays=[{"stray": "x", "match": "name = 'a\\b'"}],
        excluded=[],
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


_ATTRIBUTE_FILTER = re.compile(
    r"mapFilter\(\s*\(\s*(?P<key>\w+)\s*,\s*\w+\s*\)\s*->\s*(?P<subject>\w+)\s+"
    r"(?P<negated>NOT\s+)?IN\s*\((?P<keys>[^()]*)\)"
    r"(?:\s+AND\s+NOT\s+endsWith\(\s*(?P<suffix_subject>\w+)\s*,\s*'(?P<suffix>[^']+)'\s*\))?"
    r"\s*,\s*(?P<source>attributes_string|mapApply\(.*?'\^(?P<old>[^']+)',\s*'(?P<new>[^']+)'\),"
    r"\s*v\)\s*,\s*attributes_string\))\s*\)",
    re.DOTALL,
)


def _filter_attributes(select: str, attributes: dict[str, str]) -> dict[str, str]:
    """Apply the projection's ``mapFilter`` predicate to ``attributes``, as ClickHouse would.

    The predicate must be a lambda over the key testing membership in a tuple of string
    literals, optionally excluding a key suffix, over the attributes or a prefix-renaming
    ``mapApply`` of them; any other shape fails loudly rather than being approximated.
    """
    (match,) = _ATTRIBUTE_FILTER.finditer(select)
    assert match["subject"] == match["key"], "predicate must test the attribute key"
    assert match["suffix_subject"] in (None, match["key"]), "suffix must test the attribute key"
    listed = re.findall(r"'([^']*)'", match["keys"])
    assert re.fullmatch(r"[\s,]*", re.sub(r"'[^']*'", "", match["keys"])), "keys must be literals"
    if match["old"] is not None:
        old = match["old"].replace("\\\\", "\\").replace("\\.", ".")
        attributes = {
            (match["new"] + k[len(old) :] if k.startswith(old) else k): v
            for k, v in attributes.items()
        }
    keep_listed = match["negated"] is None
    return {
        k: v
        for k, v in attributes.items()
        if (k in listed) == keep_listed and not (match["suffix"] and k.endswith(match["suffix"]))
    }


_HARMLESS = {"gen_ai.tool.name": "bash", "session.id": "s1", "model": "m", "event.name": "e"}


@pytest.mark.parametrize(
    ("template", "sensitive"),
    [
        (
            "select_logs.sql",
            ["prompt", "arguments", "output", "content", "user.email", "user.account_id"],
        ),
        (
            "select_spans.sql",
            [
                "user_prompt",
                "prompt",
                "gen_ai.tool.description",
                "pi.gen_ai.tool.call.intent",
                "omp.gen_ai.tool.call.intent",
                "user.email",
                "user.id",
                "organization.id",
            ],
        ),
    ],
)
def test_projection_drops_raw_text_and_identity_attributes_and_keeps_the_rest(
    template: str, sensitive: list[str]
) -> None:
    select = facts.render_select(template, "1")
    attributes = {**_HARMLESS, **dict.fromkeys(sensitive, "secret")}

    assert _filter_attributes(select, attributes) == _HARMLESS


def test_attribute_filter_evaluator_rejects_a_retaining_predicate() -> None:
    select = facts.render_select("select_spans.sql", "1")
    retaining = select.replace("k NOT IN (", "k IN (", 1)
    moved = select.replace("'user_prompt', ", "", 1)

    assert "user_prompt" in _filter_attributes(retaining, {"user_prompt": "x"})
    assert "user_prompt" in _filter_attributes(moved, {"user_prompt": "x"})


def test_span_projection_normalizes_status() -> None:
    select = facts.render_select("select_spans.sql", "1")
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

    with pytest.raises(FactsError, match="Code: 62"):
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


def test_codex_commands_come_from_cmd_or_nested_exec_code_and_bypass_scans_all_arguments() -> None:
    select = facts.render_select("select_logs.sql", "1")

    command = select.split("AS cmd,")[0].rsplit("AS json_cmd,", 1)[1]
    assert "json_cmd != ''" in command
    assert "cmd" in command.split("extract(args,")[1]
    bypass = select.split("'x.gate_bypass', toString(match(")[1]
    assert bypass.startswith("args,")
    targets = select.split("AS targets,")[0].rsplit("AS command_paths,", 1)[1]
    assert "command_paths" in targets
    assert "'~'" in targets


def test_snapshots_bound_span_and_log_reads_to_the_horizon() -> None:
    for snapshot in facts.SNAPSHOTS:
        refresher = facts.snapshot_statements(snapshot)[-1]

        assert "additional_table_filters = {'introspection.spans': " in refresher
        assert f"INTERVAL {facts.SNAPSHOT_DAYS + 1} DAY" in refresher
        assert "'introspection.logs': " in refresher


@pytest.mark.parametrize(
    ("args", "bypass"),
    [
        ('{"cmd":"HUSKY=0 git commit -m x"}', True),
        ('{"cmd":"SKIP=lint git commit"}', True),
        ('["bash","-lc","HUSKY=0 git push"]', True),
        ('{"cmd":"git commit --no-verify"}', True),
        ('{"cmd":"env FOO=1 HUSKY=0 git commit"}', True),
        ('{"cmd":"echo NOHUSKY=0"}', False),
        ('{"cmd":"git commit -m x"}', False),
    ],
)
def test_gate_bypass_matches_leading_environment_assignments(args: str, bypass: bool) -> None:
    select = facts.render_select("select_logs.sql", "1")
    literal = select.split("'x.gate_bypass', toString(match(args, '")[1].split("')")[0]
    pattern = literal.replace("\\\\", "\\").replace("\\x27", "'")

    assert bool(re.search(pattern, args)) is bypass


def test_failure_signature_never_falls_back_to_an_arbitrary_output_line() -> None:
    select = facts.render_select("select_logs.sql", "1")

    assert "output_lines[1]" not in select
    signature = select.split("'x.failure_signature', if(")[1].split("'x.")[0]
    assert "error_window" in signature
    error_line = select.split("AS error_line,")[0].rsplit("arrayFirst(", 1)[1]
    assert "(?i)(error|fail" in error_line


def test_span_status_never_falls_back_to_an_arbitrary_line() -> None:
    select = facts.render_select("select_spans.sql", "1")

    assert "status_lines[1]" not in select
    status_line = select.split("AS status_line\n")[0].rsplit("arrayFirst(", 1)[1]
    assert "(?i)(error|fail" in status_line


def test_span_sweep_keys_on_end_time_so_long_spans_are_loaded() -> None:
    sweep = next(loader for loader in facts.LOADERS if loader.name == "sweep_spans")

    assert "timestamp + toIntervalNanosecond(duration_nano) > now() - INTERVAL 3 DAY" in (
        sweep.window
    )
    assert "timestamp > now() - INTERVAL 3 DAY" not in sweep.window


@pytest.mark.parametrize(
    ("select_file", "line", "window", "stored"),
    [
        ("select_spans.sql", "status_line", "status_window", "AS status_message"),
        ("select_logs.sql", "error_line", "error_window", "'x.failure_signature'"),
    ],
)
def test_capped_text_starts_at_the_keyword_when_it_lies_past_the_cap(
    select_file: str, line: str, window: str, stored: str
) -> None:
    select = facts.render_select(select_file, "1")

    chosen = select.split(f"AS {window}")[0].rsplit("if(", 1)[1]
    assert f"match(substring({line}, 1, 160), '(?i)(error|fail" in chosen
    assert f"extract({line}, '(?i)((error|fail" in chosen
    assert ".*)')" in chosen
    if stored.startswith("AS"):
        capped = select.split(stored)[0].rsplit("substring(", 1)[1]
    else:
        capped = select.split(stored)[1].split("'x.")[0]
    assert window in capped
    assert f"({line}," not in capped


def test_split_statements_ignores_semicolons_in_literals_identifiers_and_comments() -> None:
    sql = (
        "-- header; with a semicolon\n"
        "CREATE TABLE t (a String) ENGINE = Memory;\n"
        "SELECT 'a;b', 'it''s;', 'esc\\';', `x;y`, \"z;w\" /* c; */ FROM t;\n"
        "-- trailing comment only;\n"
    )

    assert facts.split_statements(sql) == [
        "-- header; with a semicolon\nCREATE TABLE t (a String) ENGINE = Memory",
        "SELECT 'a;b', 'it''s;', 'esc\\';', `x;y`, \"z;w\" /* c; */ FROM t",
    ]


def test_split_statements_matches_the_statement_count_of_every_install_file() -> None:
    for name in ("001_tables.sql", "002_registry.sql", "003_views.sql"):
        text = facts._sql(name)
        heads = re.findall(r"^(?:CREATE|DROP|INSERT|ALTER)\b", text, flags=re.MULTILINE)

        assert len(facts.split_statements(text)) == len(heads)
    for statement in facts.install_statements():
        if not statement.startswith("--"):
            assert facts.split_statements(statement) == [statement.strip()]


class _Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def _http_config(password_env: str | None = "CH_PASSWORD") -> AppConfig:
    from agent_introspection.config import parse_config

    signoz: dict[str, str] = {"clickhouse_url": "http://localhost:8123", "clickhouse_user": "facts"}
    if password_env is not None:
        signoz["clickhouse_password_env"] = password_env
    return parse_config({"signoz": signoz})


def test_http_runner_sends_one_statement_per_request_with_basic_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import base64
    import urllib.request

    requests: list[Any] = []

    def fake_urlopen(request: Any, timeout: float) -> _Response:
        requests.append((request, timeout))
        return _Response(b"1\n")

    monkeypatch.setenv("CH_PASSWORD", "s3cret")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    assert facts.http_runner(_http_config())("SELECT 1;\nSELECT ';';") == "1\n1\n"
    assert [request.data for request, _ in requests] == [b"SELECT 1", b"SELECT ';'"]
    request, timeout = requests[0]
    assert request.full_url == "http://localhost:8123/?wait_end_of_query=1"
    expected = base64.b64encode(b"facts:s3cret").decode()
    assert request.get_header("Authorization") == f"Basic {expected}"
    assert timeout == pytest.approx(600.0)
    assert facts.runner(_http_config()).__qualname__.startswith("http_runner")
    assert facts.runner(AppConfig()).__qualname__.startswith("docker_runner")


def test_http_runner_surfaces_the_exception_line_and_never_the_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import io
    import urllib.error
    import urllib.request
    from email.message import Message

    from agent_introspection.config import ConfigurationError

    def failing(request: Any, timeout: float) -> _Response:
        body = io.BytesIO(b"Code: 497. DB::Exception: facts: Not enough privileges\ntrace")
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", Message(), body)

    monkeypatch.setattr(urllib.request, "urlopen", failing)
    monkeypatch.setenv("CH_PASSWORD", "s3cret")

    with pytest.raises(FactsError, match="Code: 497") as raised:
        facts.http_runner(_http_config())("SELECT 1")
    assert "s3cret" not in str(raised.value)

    monkeypatch.delenv("CH_PASSWORD")
    with pytest.raises(ConfigurationError, match="CH_PASSWORD") as missing:
        facts.http_runner(_http_config())
    assert "s3cret" not in str(missing.value)

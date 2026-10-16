from __future__ import annotations

import itertools
import json
import re
import subprocess
import tomllib
from datetime import UTC, datetime
from typing import Any

import pytest

from agent_introspection import facts, hooks
from agent_introspection.config import AppConfig, ConfigurationError, SigNozConfig
from agent_introspection.facts import FactsError


def test_every_loader_renders_its_window_into_an_append_refresh() -> None:
    for loader in facts.LOADERS:
        ddl = facts.loader_ddl(loader)

        assert ddl.startswith(f"CREATE MATERIALIZED VIEW introspection.{loader.name}")
        retry = (
            facts.LOADER_RETRY_SETTINGS
            if loader.schedule == facts.MINUTE_SCHEDULE
            else facts.SWEEP_RETRY_SETTINGS
        )
        assert (
            f"REFRESH {loader.schedule}\nSETTINGS {retry}\n"
            f"APPEND TO introspection.{loader.target} EMPTY\nAS\n"
        ) in ddl
        assert loader.window in ddl
        assert "{window}" not in ddl
        # Refresh-strategy settings plus the query's own memory-bounded settings.
        assert ddl.count("SETTINGS") == 2
        assert ddl.endswith(f"\nSETTINGS {facts.LOAD_SETTINGS}")
        assert "max_memory_usage = 1610612736" in ddl


def test_span_loaders_select_by_end_time_and_log_loader_by_ingest_time() -> None:
    windows = {loader.name: loader.window for loader in facts.LOADERS}

    assert "toIntervalNanosecond(duration_nano)" in windows["load_spans"]
    assert windows["load_logs"].startswith("observed_timestamp")


def test_record_loaders_window_on_collector_time_and_filter_their_family() -> None:
    loaders = {loader.name: loader for loader in facts.LOADERS}
    minute, sweep = loaders["load_hook_events"], loaders["sweep_hook_events"]

    assert (minute.schedule, minute.target) == ("EVERY 1 MINUTE", "hook_events")
    assert sweep.schedule.startswith("EVERY 1 HOUR OFFSET ")
    assert sweep.target == "hook_events"
    assert "INTERVAL 30 MINUTE" in minute.window
    assert "INTERVAL 3 DAY" in sweep.window
    for loader in (minute, sweep):
        assert loader.window.startswith("observed_timestamp")
        assert "timestamp >" not in loader.window.replace("observed_timestamp", "")
        ddl = facts.loader_ddl(loader)
        assert "resources_string['service.name'] = 'agent-introspection'" in ddl
        assert "attributes_string['event.name'] = 'agent_introspection.hook_event'" in ddl
        assert "JSONExtractString(body, 'schema') = 'agent-introspection.hook-event/1'" in ddl


def test_install_drops_the_removed_attribution_objects_loaders_first() -> None:
    statements = facts.install_statements()
    drops = [statements.index(drop) for drop in facts.RETIRED_OBJECTS]

    assert drops == sorted(drops)
    assert statements[drops[0] - 1] == facts._sql("001_tables.sql")
    assert facts.RETIRED_OBJECTS[-1] == "DROP TABLE IF EXISTS introspection.session_projects"
    assert not any("session_project" in loader.target for loader in facts.LOADERS)


def test_no_select_reads_the_agent_introspection_service_without_a_family_filter() -> None:
    for name in ("select_logs.sql", "select_spans.sql"):
        assert "agent-introspection" not in facts._sql(name)
    select = facts._sql("select_hook_events.sql")
    assert select.count("'agent-introspection'") == 1
    assert select.count("attributes_string['event.name']") == 1
    assert select.count("JSONExtractString(body, 'schema')") == 1


def test_hook_event_select_flattens_attrs_by_json_type() -> None:
    # Numbers and flags go to attrs_number as Float64, text to attrs_string, and every
    # other JSON value (lists, objects, null) to JSON text, as the hook record defines.
    select = facts._sql("select_hook_events.sql")

    assert "'Map(LowCardinality(String), String)'" in select
    assert "'Map(LowCardinality(String), Float64)'" in select
    assert select.count("IN ('Int64', 'UInt64', 'Double', 'Bool')") == 2
    assert "NOT IN ('Int64', 'UInt64', 'Double', 'Bool')" in select
    assert "toFloat64(JSONExtractBool(body, 'attrs', k))" in select
    assert "JSONExtractRaw(body, 'attrs', k)" in select
    assert "resources_string['host.name'] AS host" in select
    # Added columns come last: backfills insert by position.
    assert select.index("AS loaded_at") < select.index("AS host")


def test_tables_add_the_cross_machine_columns_after_their_create() -> None:
    statements = facts.split_statements(facts._sql("001_tables.sql"))

    def index(fragment: str) -> int:
        return next(i for i, s in enumerate(statements) if fragment in s)

    alter = index("ALTER TABLE introspection.hook_events ADD COLUMN IF NOT EXISTS host ")
    assert index("CREATE TABLE IF NOT EXISTS introspection.hook_events\n") < alter


def test_home_redaction_covers_users_home_and_root_everywhere() -> None:
    for name in ("select_logs.sql", "select_spans.sql"):
        select = facts._sql(name)
        assert "/Users/[" not in select
    assert facts._sql("select_logs.sql").count("/root(") == 3
    assert facts._sql("select_spans.sql").count("/root(") == 2


def test_install_orders_tables_loaders_views_snapshots_then_registry_rows() -> None:
    statements = facts.install_statements()

    def first(fragment: str) -> int:
        return next(i for i, s in enumerate(statements) if fragment in s)

    assert "CREATE TABLE IF NOT EXISTS introspection.spans" in statements[0]
    assert (
        "CREATE OR REPLACE TABLE introspection.signal_support"
        in statements[first("CREATE OR REPLACE TABLE introspection.signal_support")]
    )
    checks = [i for i, s in enumerate(statements) if s.endswith("FORMAT Null")]
    assert checks
    # The live registry is replaced only after every route predicate has validated.
    assert max(checks) < first("CREATE OR REPLACE TABLE introspection.signal_support")
    assert max(checks) < first("INSERT INTO introspection.harnesses")
    loaders = [s for s in statements if "APPEND TO" in s]
    assert len(loaders) == len(facts.LOADERS)
    views = first("CREATE OR REPLACE VIEW introspection.usage_events")
    assert first("APPEND TO") < views < first("CREATE MATERIALIZED VIEW introspection.refresh_")
    # Snapshot refreshers are dropped before loaders because they depend on them.
    assert first("DROP VIEW IF EXISTS introspection.refresh_") < first(
        "DROP VIEW IF EXISTS introspection.load_spans"
    )
    # task_labels reads task_outcomes_snapshot, which a fresh database has only once created.
    assert first("CREATE VIEW introspection.task_outcomes_snapshot") < first(
        "CREATE OR REPLACE VIEW introspection.task_labels"
    )
    assert statements[-1].startswith("INSERT INTO introspection.signal_exclusions")


def test_verify_builds_only_a_scratch_store_that_never_refreshes() -> None:
    statements = facts.verify_statements()
    refreshers = [s for s in statements if "REFRESH EVERY" in s]

    scratch = re.fullmatch(
        r"DROP DATABASE IF EXISTS (introspection_verify_\d+_\d+) SYNC", statements[0]
    )
    assert scratch
    database = scratch.group(1)
    assert not [s for s in statements if re.search(r"\bintrospection[.;]", s)]
    assert len(refreshers) == len(facts.LOADERS) + 2 * len(facts.SNAPSHOTS)
    for refresher in refreshers:
        assert "REFRESH EVERY 1 YEAR" in refresher
        assert " EMPTY\nAS" in refresher
        assert "refresh_retries" in refresher
    # A rejected history offset must fail in the scratch database, so it survives.
    history = [s for s in refreshers if "_history\nREFRESH" in s]
    assert len(history) == len(facts.SNAPSHOTS)
    assert all(re.search(r"EVERY 1 YEAR OFFSET \d+ MINUTE DEPENDS", s) for s in history)
    # Recent and sweep offsets survive the rewrite too, so a rejected one fails here.
    recent = [s for s in refreshers if "_recent\nREFRESH" in s]
    assert len(recent) == len(facts.SNAPSHOTS)
    assert all(re.search(r"EVERY 1 YEAR OFFSET \d+ MINUTE DEPENDS", s) for s in recent)
    sweeps = [s for s in refreshers if f"{database}.sweep_" in s]
    assert len(sweeps) == 3
    assert all(re.search(r"REFRESH EVERY 1 YEAR OFFSET \d+ MINUTE\n", s) for s in sweeps)


def test_install_stops_before_the_live_store_when_a_statement_is_rejected() -> None:
    ran: list[str] = []

    def run(sql: str) -> str:
        ran.append(sql)
        if re.search(r"VIEW introspection_verify_\d+_\d+\.task_labels", sql):
            raise facts.FactsError("Unknown table expression identifier")
        return ""

    with pytest.raises(facts.FactsError):
        facts.install(run)

    assert not [s for s in ran if re.search(r"\bintrospection[.;]", s)]
    assert re.fullmatch(r"DROP DATABASE IF EXISTS introspection_verify_\d+_\d+ SYNC", ran[-1])


def _refresher_settings(refresher: str) -> dict[str, str]:
    """Return a snapshot refresher's own settings, without its table-filter map."""
    clause = refresher.rsplit("SETTINGS ", 1)[1].split(", additional")[0]
    return dict(part.split(" = ", 1) for part in clause.split(", "))


def test_every_fact_view_has_a_snapshot_split_into_replaced_history_and_recent_parts() -> None:
    for snapshot in facts.SNAPSHOTS:
        statements = facts.snapshot_statements(snapshot)
        refreshers = [s for s in statements if s.startswith("CREATE MATERIALIZED VIEW")]

        assert any(
            s.startswith(f"CREATE VIEW introspection.{snapshot.view}_snapshot") for s in statements
        )
        assert len(refreshers) == 2
        for refresher in refreshers:
            # Each part replaces its table whole, so it never holds duplicates.
            assert "APPEND" not in refresher
            # Creation must not fire the refresher: install refreshes them one at a time.
            assert re.search(r"\nTO introspection\.\w+ EMPTY\nAS SELECT", refresher)
            assert (
                "DEPENDS ON introspection.load_spans, introspection.load_logs,"
                " introspection.load_hook_events"
            ) in refresher
            # A refresher must spill before it reaches a memory ceiling below the server's.
            settings = _refresher_settings(refresher)
            ceiling = int(settings["max_memory_usage"])
            assert ceiling <= 2 * 1024**3
            assert int(settings["max_bytes_before_external_group_by"]) <= ceiling // 2
            assert int(settings["max_bytes_before_external_sort"]) <= ceiling // 2
            assert "grace_hash" in settings["join_algorithm"]
    labels = next(s for s in facts.SNAPSHOTS if s.view == "task_labels")
    waits = [s for s in facts.snapshot_statements(labels) if "DEPENDS ON" in s]
    assert "introspection.refresh_task_outcomes_history" in waits[0]
    assert "introspection.refresh_task_outcomes_recent" in waits[1]
    views = "".join(facts._sql(name) for name in facts.VIEW_FILES)
    fact_views = {
        name.split(" ")[0] for name in views.split("CREATE OR REPLACE VIEW introspection.")[1:]
    }
    # Lookup views (hook tool details, hook harness) are not snapshotted.
    helpers = {"hook_rows", "hook_tool_calls"}
    assert {s.view for s in facts.SNAPSHOTS} == fact_views - helpers


def test_compare_rebuilds_sources_first_and_fails_on_any_mismatch() -> None:
    ran: list[str] = []

    def run(sql: str) -> str:
        ran.append(sql)
        if "FORMAT JSONEachRow" not in sql:
            return ""
        held = "2" if "introspection.tool_calls_snapshot" in sql else "1"
        return (
            f'{{"side":"snapshot","rows":"5","hash":"{held}"}}\n'
            '{"side":"view","rows":"5","hash":"1"}\n'
        )

    result = facts.compare(run)

    assert result["passed"] is False
    assert [v for v, r in result["snapshots"].items() if not r["match"]] == ["tool_calls"]
    refreshed = [s for s in ran if s.startswith("SYSTEM REFRESH VIEW")]
    assert refreshed.index("SYSTEM REFRESH VIEW introspection.refresh_task_outcomes_recent") < (
        refreshed.index("SYSTEM REFRESH VIEW introspection.refresh_task_labels_recent")
    )


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
                "claude-code": {"alignment": "not emitted"},
            },
            "needs a note",
        ),
        (
            {
                "codex": {"route": "codex.usage", "alignment": "aligned"},
                "oh-my-pi": {"route": "omp.chat", "alignment": "aligned"},
                "claude-code": {"route": "claude.usage", "alignment": "not emitted", "note": "n"},
            },
            "not emitted has no route",
        ),
        (
            {
                "codex": {"alignment": "not emitted", "note": "n"},
                "oh-my-pi": {"alignment": "not emitted", "note": "n"},
                "claude-code": {"alignment": "not applicable", "note": "n"},
            },
            "no harness emits it",
        ),
    ],
)
def test_registry_rejects_incomplete_or_undisclosed_support(
    support: dict[str, dict[str, str]], message: str
) -> None:
    with pytest.raises(facts.RegistryError, match=message):
        facts.parse_registry(_document(**support))


def _visibility_registry() -> facts.Registry:
    return facts.parse_registry(
        _document(
            codex={"alignment": "not emitted", "note": "no duration"},
            codex_exec={"alignment": "not applicable", "note": "headless"},
            **{
                "oh-my-pi": {"route": "omp.chat", "alignment": "aligned"},
                "claude-code": {"route": "claude.usage", "alignment": "differs", "note": "n"},
            },
        )
    )


def test_not_emitted_cells_hide_a_signal_only_where_that_harness_is_enabled() -> None:
    registry = _visibility_registry()

    every = facts.enabled_harnesses(registry, None)
    assert facts.hidden_signals(registry, every) == [
        {
            "signal": "s",
            "view": "cache",
            "title": "t",
            "missing": "codex-app-server, codex_cli_rs",
            "note": "no duration",
        }
    ]
    # Not applicable still reaches the signal, so Codex exec alone keeps it visible.
    assert facts.hidden_signals(registry, ("claude-code", "oh-my-pi", "codex_exec")) == []
    assert facts.hidden_signals(registry, ("claude-code", "codex_cli_rs"))[0]["missing"] == (
        "codex_cli_rs"
    )


def test_enabled_harnesses_expand_codex_keep_registry_order_and_reject_unknown_keys() -> None:
    registry = _visibility_registry()

    assert facts.enabled_harnesses(registry, None) == tuple(registry.harnesses)
    assert facts.enabled_harnesses(registry, ["claude-code", "codex"]) == (
        "codex-app-server",
        "codex_cli_rs",
        "codex_exec",
        "claude-code",
    )
    with pytest.raises(ConfigurationError, match="unknown harness gemini"):
        facts.enabled_harnesses(registry, ["claude-code", "gemini"])


def test_excluded_is_only_for_signals_no_harness_produces() -> None:
    document = _document(
        codex={"route": "codex.usage", "alignment": "aligned"},
        **{
            "oh-my-pi": {"route": "omp.chat", "alignment": "aligned"},
            "claude-code": {"route": "claude.usage", "alignment": "aligned"},
        },
    )
    document["excluded"] = [
        {"id": "x", "view": "cache", "title": "x", "missing": ["oh-my-pi"], "reason": "r"}
    ]

    with pytest.raises(facts.RegistryError, match="no harness produces"):
        facts.parse_registry(document)


def test_repo_registry_lists_harness_specific_gaps_as_not_emitted_signals() -> None:
    registry = facts.load_registry()
    restored = {
        "usage.cache_creation",
        "guard.sandbox",
        "provider.stream_disconnects",
        "provider.throughput",
        "provider.model_conformance",
    }

    assert [row["signal"] for row in registry.excluded] == ["intervene.rule_adherence"]
    every = facts.enabled_harnesses(registry, None)
    assert {row["signal"] for row in facts.hidden_signals(registry, every)} == restored
    # A machine without Codex shows throughput; with only Codex it shows stream disconnects.
    no_codex = facts.enabled_harnesses(registry, ["claude-code", "oh-my-pi"])
    assert "provider.throughput" not in {
        row["signal"] for row in facts.hidden_signals(registry, no_codex)
    }
    codex_only = facts.enabled_harnesses(registry, ["codex"])
    assert "provider.stream_disconnects" not in {
        row["signal"] for row in facts.hidden_signals(registry, codex_only)
    }


def test_install_loads_this_machines_harnesses_before_the_registry_rows() -> None:
    statements = facts.install_statements(["claude-code", "codex"])

    harnesses = next(s for s in statements if "INSERT INTO introspection.harnesses" in s)
    assert '{"harness": "oh-my-pi", "label": "omp", "enabled": 0}' in harnesses
    assert '{"harness": "codex_exec", "label": "Codex exec", "enabled": 1}' in harnesses
    create = next(
        i
        for i, s in enumerate(statements)
        if "CREATE OR REPLACE TABLE introspection.harnesses" in s
    )
    assert create < statements.index(harnesses)
    with pytest.raises(ConfigurationError):
        facts.install_statements(["claude"])
    every = next(s for s in facts.install_statements() if "introspection.harnesses SELECT" in s)
    assert '"enabled": 0' not in every


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

    (statement,) = facts.registry_statements(registry, ())

    assert statement.startswith(
        "INSERT INTO introspection.signal_strays SELECT * FROM format(JSONEachRow, '"
    )
    assert "\\'a" in statement


def test_backfill_windows_cover_the_range_exactly() -> None:
    start = datetime(2026, 9, 1, tzinfo=UTC)
    end = datetime(2026, 9, 3, 12, 30, tzinfo=UTC)

    windows = facts.backfill_windows(start, end)

    assert windows[0][0] == start
    assert windows[-1][1] == end
    assert all(a[1] == b[0] for a, b in itertools.pairwise(windows))
    assert all(lo < hi and hi - lo <= facts.BACKFILL_WINDOW for lo, hi in windows)
    assert len(windows) == 11  # 60h30m: ten full windows and one clipped


def test_backfill_statements_are_memory_bounded_inserts() -> None:
    start = datetime(2026, 9, 1, tzinfo=UTC)
    end = datetime(2026, 9, 1, 13, tzinfo=UTC)

    statements = facts.backfill_statements("logs", start, end)

    assert len(statements) == 3
    assert "'2026-09-01 00:00:00'" in statements[0]
    assert "'2026-09-01 13:00:00'" in statements[-1]
    for statement in statements:
        assert statement.startswith("INSERT INTO introspection.logs")
        assert statement.count("SETTINGS") == 1
        assert statement.rstrip().endswith(f"SETTINGS {facts.LOAD_SETTINGS}")
        assert "max_memory_usage = 1610612736" in statement
        assert "max_threads = 2" in statement


def test_backfill_failure_names_the_target_and_window() -> None:
    def run(sql: str) -> str:
        if "INSERT INTO introspection.logs" in sql:
            raise facts.FactsError("Code: 241")
        return "0\n" if sql.startswith("SELECT count()") else ""

    with pytest.raises(facts.FactsError, match=r"backfill of logs failed for window 2026-09-27"):
        facts.backfill(run, days=1, now=datetime(2026, 9, 28, 6, tzinfo=UTC))


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
    normalising = select.split("AS status_normalised")[0]
    assert "'~\\\\1'" in normalising
    assert "'N'" in normalising


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
    monkeypatch.setattr(facts, "_require_local_docker_context", lambda context: None)

    with pytest.raises(FactsError, match="Code: 62"):
        facts.docker_runner(AppConfig())("SELECT 1")

    assert calls[0]["input"] == "SELECT 1"
    assert calls[0]["argv"][-2:] == ("clickhouse-client", "--multiquery")


def _inspect(monkeypatch: pytest.MonkeyPatch, host: str) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def fake_run(argv: tuple[str, ...], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        payload = json.dumps([{"Endpoints": {"docker": {"Host": host}}}])
        return subprocess.CompletedProcess(argv, 0, payload, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


@pytest.mark.parametrize("host", ["unix:///var/run/docker.sock", "tcp://127.0.0.1:2375"])
def test_docker_context_accepts_local_daemons(monkeypatch: pytest.MonkeyPatch, host: str) -> None:
    calls = _inspect(monkeypatch, host)

    facts._require_local_docker_context("desktop")

    assert calls == [("docker", "context", "inspect", "desktop")]


@pytest.mark.parametrize("host", ["ssh://me@build.example.com", "tcp://10.1.2.3:2376"])
def test_docker_context_rejects_remote_daemons_before_any_statement(
    monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    calls = _inspect(monkeypatch, host)
    config = AppConfig(signoz=SigNozConfig(docker_context="remote"))

    with pytest.raises(ConfigurationError, match="on this machine"):
        facts.docker_runner(config)("SELECT 1")

    assert calls == [("docker", "context", "inspect", "remote")]


def test_backfill_reports_final_row_counts() -> None:
    executed: list[str] = []

    def run(sql: str) -> str:
        executed.append(sql)
        return "7\n" if sql.startswith("SELECT count()") else ""

    result = facts.backfill(run, days=1, now=datetime(2026, 9, 28, 6, tzinfo=UTC))

    assert result["rows"] == {"spans": 7, "logs": 7, "hook_events": 7}
    assert result["start"] == "2026-09-27T00:00:00+00:00"
    assert sum(sql.startswith("INSERT INTO") for sql in executed) == 3 * 5
    inserted = {sql.split("\n")[0] for sql in executed if sql.startswith("INSERT INTO")}
    assert inserted == {f"INSERT INTO introspection.{t}" for t in ("spans", "logs", "hook_events")}


def test_codex_commands_come_from_cmd_or_nested_exec_code_and_bypass_scans_all_arguments() -> None:
    select = facts.render_select("select_logs.sql", "1")

    command = select.split("AS cmd,")[0].rsplit("AS json_cmd,", 1)[1]
    assert "json_cmd != ''" in command
    assert "cmd" in command.split("extract(args,")[1]
    bypass = select.split("'x.gate_bypass', toString(match(")[1]
    assert bypass.startswith("args,")
    targets = select.split("AS targets,")[0].rsplit("AS command_paths,", 1)[1]
    assert "command_paths" in targets
    assert "'~\\\\1'" in targets


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
    ("select_file", "line", "window", "normalised"),
    [
        ("select_spans.sql", "status_line", "status_window", "status_normalised"),
        ("select_logs.sql", "error_line", "error_window", "error_normalised"),
    ],
)
def test_capped_text_starts_at_the_keyword_when_it_lies_past_the_cap(
    select_file: str, line: str, window: str, normalised: str
) -> None:
    select = facts.render_select(select_file, "1")

    chosen = select.split(f"AS {window}")[0].rsplit("if(", 1)[1]
    assert f"match(substringUTF8({line}, 1, 80), '(?i)(error|fail" in chosen
    assert f"extract({line}, '(?i)((error|fail" in chosen
    assert ".*)')" in chosen
    assert "160" not in select
    assert f"{window}," in select.split(f"AS {window}")[1].split(f"AS {normalised}")[0]


def test_span_projection_stores_a_hash_of_the_uncapped_normalized_line() -> None:
    select = facts.render_select("select_spans.sql", "1")

    assert "'x.status_hash', lower(substring(hex(SHA256(status_normalised)), 1, 16))" in select
    assert "status_normalised = ''" in select


def test_log_projection_stores_a_hash_of_the_uncapped_normalized_line() -> None:
    select = facts.render_select("select_logs.sql", "1")

    signature = select.split("'x.failure_signature', if(")[1]
    signature = signature.split("'x.failure_signature_hash'")[0]
    assert re.search(r"AS error_normalised,\s+1,\s+80\s*\)", signature)
    digest = select.split("'x.failure_signature_hash', if(")[1].split("\n                ),")[0]
    assert "failed AND error_normalised != ''" in digest
    assert "lower(substring(hex(SHA256(error_normalised)), 1, 16))" in digest


def test_tool_calls_view_keeps_policy_blocked_calls_out_of_failures_and_signatures() -> None:
    views = facts._sql("003_views.sql")
    view = views.split("CREATE OR REPLACE VIEW introspection.tool_calls AS")[1].split("\n);")[0]

    outcome = view.split("attrs_string['pi.gen_ai.tool.status'] = 'ok'")[1].split("'unknown'")[0]
    assert outcome.index("= 'blocked', 'policy_blocked'") < outcome.index("has_error, 'failed'")
    assert view.count("attrs_string['pi.gen_ai.tool.status'] != 'blocked'") == 2


def test_tool_calls_view_unifies_old_and_new_signature_rows_for_every_source() -> None:
    views = facts._sql("003_views.sql")
    view = views.split("CREATE OR REPLACE VIEW introspection.tool_calls AS")[1].split("\n);")[0]

    # Every source passes its stored text and hash through one shared derivation.
    assert view.count("AS stored_signature") == 1
    assert view.count("AS stored_hash") == 1
    for stored in (
        "attrs_string['x.failure_signature_hash']",
        "attrs_string['x.status_hash']",
        "hf.hf_signature_hash",
    ):
        assert stored in view
    assert "substringUTF8(windowed_signature, 1, 80) AS failure_signature" in view
    assert "length(stored_signature) < 160" in view
    assert "* EXCEPT (stored_signature, stored_hash, windowed_signature)" in view


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
    for name in ("001_tables.sql", "002_registry.sql", *facts.VIEW_FILES):
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

    body = io.BytesIO(b"Code: 497. DB::Exception: facts: Not enough privileges\ntrace")

    def failing(request: Any, timeout: float) -> _Response:
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", Message(), body)

    monkeypatch.setattr(urllib.request, "urlopen", failing)
    monkeypatch.setenv("CH_PASSWORD", "s3cret")

    with pytest.raises(FactsError, match="Code: 497") as raised:
        facts.http_runner(_http_config())("SELECT 1")
    assert "s3cret" not in str(raised.value)
    assert body.closed

    monkeypatch.delenv("CH_PASSWORD")
    with pytest.raises(ConfigurationError, match="CH_PASSWORD") as missing:
        facts.http_runner(_http_config())
    assert "s3cret" not in str(missing.value)


@pytest.mark.parametrize("template", ["select_logs.sql", "select_spans.sql"])
def test_projection_redacts_credentials_before_normalizing_diagnostics(template: str) -> None:
    select = facts.render_select(template, "1")

    def sql_literal(text: str) -> str:
        return "'" + text.replace("\\", "\\\\").replace("'", "''") + "'"

    # Every Python redaction, in order, appears verbatim in the SQL projection.
    position = -1
    for pattern, repl in hooks.REDACTIONS:
        found = select.find(sql_literal(pattern), position + 1)
        assert found > position, pattern
        assert sql_literal(repl) in select[found:]
        position = found
    # Redaction wraps the window before home and digit normalization.
    assert select.index("[REDACTED]") < select.index("'/(?:Users|home)/[^/ ]+|/root($|[/ ])',\n")


def test_registry_harnesses_reads_the_harness_table_and_refuses_an_empty_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert facts.registry_harnesses() == tuple(facts.load_registry().harnesses)

    monkeypatch.setattr(facts, "_sql", lambda _name: "[harnesses]\n")
    facts.registry_harnesses.cache_clear()
    try:
        with pytest.raises(facts.RegistryError, match=r"needs \[harnesses\]"):
            facts.registry_harnesses()
    finally:
        facts.registry_harnesses.cache_clear()


def test_opencode_is_a_known_producer_and_not_a_harness() -> None:
    registry = facts.load_registry()
    producers = tomllib.loads(facts._sql(facts.REGISTRY_FILE))["producers"]

    assert "opencode" in producers
    assert "opencode" not in registry.harnesses
    # Routes and strays match rows of the harnesses' facts, which never hold opencode.
    assert all(row["harness"] != "opencode" for row in [*registry.routes, *registry.strays])
    stray = {"id": "x", "harness": "opencode", "source": "spans", "match": "1", "reason": "r"}
    assert facts._parse_strays([stray], {}, producers)[0]["harness"] == "opencode"
    with pytest.raises(facts.RegistryError, match="unknown harness"):
        facts._parse_strays(
            [{"id": "x", "harness": "gemini", "source": "spans", "match": "1", "reason": "r"}],
            {},
            {},
        )

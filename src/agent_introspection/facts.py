"""ClickHouse-materialized producer facts for the dashboard.

Refreshable materialized views copy curated producer spans and logs from the
SigNoz tables into the durable ``introspection`` database every minute. Fact
views normalize them, snapshot tables hold each view's result refreshed after
every load, and the signal support registry records how each producer reaches
each dashboard signal. The dashboard reads small aggregates from the snapshots.
"""

from __future__ import annotations

import json
import subprocess
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from typing import Any, cast

from agent_introspection.config import AppConfig
from agent_introspection.source import SourceError

DATABASE = "introspection"
_QUERY_TIMEOUT_SECONDS = 600.0

type SqlRunner = Callable[[str], str]


@dataclass(frozen=True)
class Loader:
    """One refreshable materialized view that appends a sliding source window."""

    name: str
    target: str
    select_file: str
    schedule: str
    window: str


# Spans are exported when they end, and turns can run for more than a day, so the
# minute loader selects spans that ended recently. The hourly sweep re-reads three
# days to catch late exports. Logs carry an ingest time, which can trail the event
# time by days, so their loader keys on `observed_timestamp`.
LOADERS = (
    Loader(
        name="load_spans",
        target="spans",
        select_file="select_spans.sql",
        schedule="EVERY 1 MINUTE",
        window=(
            "timestamp > now() - INTERVAL 1 DAY"
            " AND timestamp + toIntervalNanosecond(duration_nano) > now() - INTERVAL 30 MINUTE"
        ),
    ),
    Loader(
        name="sweep_spans",
        target="spans",
        select_file="select_spans.sql",
        schedule="EVERY 1 HOUR",
        window="timestamp > now() - INTERVAL 3 DAY",
    ),
    Loader(
        name="load_logs",
        target="logs",
        select_file="select_logs.sql",
        schedule="EVERY 1 MINUTE",
        window=(
            "observed_timestamp"
            " > toUInt64(toUnixTimestamp(now() - INTERVAL 30 MINUTE)) * 1000000000"
            " AND timestamp > toUInt64(toUnixTimestamp(now() - INTERVAL 7 DAY)) * 1000000000"
        ),
    ),
)

_BACKFILL_WINDOWS = {
    "spans": (
        "timestamp >= toDateTime64('{start}', 9, 'UTC')"
        " AND timestamp < toDateTime64('{end}', 9, 'UTC')"
    ),
    "logs": (
        "timestamp >= toUInt64(toUnixTimestamp64Nano(toDateTime64('{start}', 9, 'UTC')))"
        " AND timestamp < toUInt64(toUnixTimestamp64Nano(toDateTime64('{end}', 9, 'UTC')))"
    ),
}
_SELECT_FILES = {"spans": "select_spans.sql", "logs": "select_logs.sql"}


@dataclass(frozen=True)
class Snapshot:
    """A table holding one fact view's result, replaced after each minute load."""

    view: str
    order_by: str


SNAPSHOTS = (
    Snapshot("usage_events", "(harness, ts)"),
    Snapshot("task_outcomes", "(harness, start_ts)"),
    Snapshot("tool_calls", "(harness, ts)"),
    Snapshot("user_signals", "(harness, ts)"),
    Snapshot("model_calls", "(harness, ts)"),
)

REGISTRY_FILE = "signal_support.toml"
_ALIGNMENTS = ("aligned", "differs", "not emitted")
_HARNESS_GROUPS = {"codex": ("codex-app-server", "codex_cli_rs", "codex_exec")}
_SOURCES = ("spans", "logs")

type Row = dict[str, str | int]


class RegistryError(ValueError):
    """The signal support registry is inconsistent."""


@dataclass(frozen=True)
class Registry:
    """Rows for the four registry tables, in file order."""

    signals: list[Row]
    routes: list[Row]
    support: list[Row]
    strays: list[Row]


def _sql(name: str) -> str:
    return files("agent_introspection.facts_sql").joinpath(name).read_text()


def render_select(select_file: str, window: str) -> str:
    """Return a curated projection restricted to one source-time predicate."""
    return _sql(select_file).replace("{window}", window)


def loader_ddl(loader: Loader) -> str:
    """Return the refreshable materialized view definition for one loader."""
    return (
        f"CREATE MATERIALIZED VIEW {DATABASE}.{loader.name}\n"
        f"REFRESH {loader.schedule} APPEND TO {DATABASE}.{loader.target}\n"
        f"AS\n{render_select(loader.select_file, loader.window)}"
    )


def snapshot_statements(snapshot: Snapshot) -> list[str]:
    """Return statements that recreate one snapshot table and its refresher.

    The refresher depends on both minute loaders, so each snapshot replaces its
    content right after new rows land. Non-append refreshes swap atomically.
    """
    table = f"{DATABASE}.{snapshot.view}_snapshot"
    return [
        f"DROP VIEW IF EXISTS {DATABASE}.refresh_{snapshot.view}",
        f"DROP TABLE IF EXISTS {table}",
        f"CREATE TABLE {table} ENGINE = MergeTree ORDER BY {snapshot.order_by}"
        f" EMPTY AS SELECT * FROM {DATABASE}.{snapshot.view}",
        f"CREATE MATERIALIZED VIEW {DATABASE}.refresh_{snapshot.view}\n"
        f"REFRESH EVERY 1 MINUTE DEPENDS ON {DATABASE}.load_spans, {DATABASE}.load_logs\n"
        f"TO {table}\nAS SELECT * FROM {DATABASE}.{snapshot.view}",
    ]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RegistryError(message)


def _text(entry: dict[str, Any], key: str, where: str) -> str:
    value = entry.get(key)
    _require(isinstance(value, str) and value.strip() != "", f"{where}: {key} must be text")
    return cast(str, value)


def _parse_routes(
    entries: list[dict[str, Any]], harnesses: dict[str, str]
) -> tuple[list[Row], dict[str, list[str]]]:
    rows: list[Row] = []
    covered_by: dict[str, list[str]] = {}
    for entry in entries:
        route = _text(entry, "id", "route")
        _require(route not in covered_by, f"duplicate route {route}")
        source = _text(entry, "source", route)
        _require(source in _SOURCES, f"{route}: source must be spans or logs")
        expect = entry.get("expect", "rows")
        _require(expect in ("rows", "events"), f"{route}: expect must be rows or events")
        covered = cast(list[str], entry.get("harnesses", []))
        _require(bool(covered) and set(covered) <= set(harnesses), f"{route}: unknown harness")
        covered_by[route] = covered
        rows.extend(
            {
                "route": route,
                "harness": harness,
                "harness_label": harnesses[harness],
                "source": source,
                "match": _text(entry, "match", route),
                "expect": expect,
                "description": _text(entry, "description", route),
            }
            for harness in covered
        )
    return rows, covered_by


def _parse_strays(entries: list[dict[str, Any]], harnesses: dict[str, str]) -> list[Row]:
    rows: list[Row] = []
    for entry in entries:
        stray = _text(entry, "id", "stray")
        harness = _text(entry, "harness", stray)
        _require(harness in harnesses, f"{stray}: unknown harness")
        source = _text(entry, "source", stray)
        _require(source in _SOURCES, f"{stray}: source must be spans or logs")
        rows.append(
            {
                "stray": stray,
                "harness": harness,
                "source": source,
                "match": _text(entry, "match", stray),
                "reason": _text(entry, "reason", stray),
            }
        )
    return rows


def _expand_support(declared: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """Expand group keys such as `codex`; a harness-specific entry wins."""
    expanded: dict[str, dict[str, str]] = {}
    for key, value in declared.items():
        for harness in _HARNESS_GROUPS.get(key, (key,)):
            if key in _HARNESS_GROUPS and harness in declared:
                continue
            expanded[harness] = value
    return expanded


def _parse_support(
    signal: str,
    unit: str,
    declared: dict[str, dict[str, str]],
    harnesses: dict[str, str],
    covered_by: dict[str, list[str]],
) -> list[Row]:
    expanded = _expand_support(declared)
    _require(set(expanded) == set(harnesses), f"{signal}: support must cover every harness")
    rows: list[Row] = []
    for harness, label in harnesses.items():
        value = expanded[harness]
        alignment = value.get("alignment", "")
        _require(alignment in _ALIGNMENTS, f"{signal}/{harness}: unknown alignment")
        route = value.get("route", "")
        note = value.get("note", "")
        if alignment == "not emitted":
            _require(route == "", f"{signal}/{harness}: not emitted has no route")
        else:
            _require(harness in covered_by.get(route, []), f"{signal}/{harness}: bad route")
        _require(
            alignment == "aligned" or note != "", f"{signal}/{harness}: {alignment} needs a note"
        )
        rows.append(
            {
                "signal": signal,
                "harness": harness,
                "harness_label": label,
                "route": route,
                "unit": unit,
                "alignment": alignment,
                "note": note,
            }
        )
    return rows


def parse_registry(document: dict[str, Any]) -> Registry:
    """Validate the registry document and flatten it into table rows."""
    harnesses = cast(dict[str, str], document.get("harnesses", {}))
    views = cast(dict[str, str], document.get("views", {}))
    _require(bool(harnesses) and bool(views), "registry needs [harnesses] and [views]")
    routes, covered_by = _parse_routes(document.get("route", []), harnesses)
    signals: list[Row] = []
    support: list[Row] = []
    for order, entry in enumerate(document.get("signal", [])):
        signal = _text(entry, "id", "signal")
        _require(all(row["signal"] != signal for row in signals), f"duplicate signal {signal}")
        view = _text(entry, "view", signal)
        _require(view in views, f"{signal}: unknown view {view}")
        scope = entry.get("scope", "harness")
        _require(scope in ("harness", "system"), f"{signal}: scope must be harness or system")
        unit = _text(entry, "unit", signal)
        signals.append(
            {
                "signal": signal,
                "view": view,
                "view_title": views[view],
                "view_order": list(views).index(view),
                "title": _text(entry, "title", signal),
                "question": _text(entry, "question", signal),
                "unit": unit,
                "formula": _text(entry, "formula", signal),
                "scope": scope,
                "sort": order,
            }
        )
        declared = cast(dict[str, dict[str, str]], entry.get("support", {}))
        if scope == "system":
            _require(not declared, f"{signal}: system signals have no per-harness support")
        else:
            support.extend(_parse_support(signal, unit, declared, harnesses, covered_by))
    return Registry(
        signals=signals,
        routes=routes,
        support=support,
        strays=_parse_strays(document.get("stray", []), harnesses),
    )


def load_registry() -> Registry:
    """Parse and validate the repo-owned signal support registry."""
    return parse_registry(tomllib.loads(_sql(REGISTRY_FILE)))


def _sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def registry_statements(registry: Registry) -> list[str]:
    """Return statements that replace the registry tables with the file's rows."""
    tables: dict[str, list[Row]] = {
        "signals": registry.signals,
        "signal_routes": registry.routes,
        "signal_support": registry.support,
        "signal_strays": registry.strays,
    }
    return [
        f"INSERT INTO {DATABASE}.{table} SELECT * FROM format(JSONEachRow, "
        + _sql_string("\n".join(json.dumps(row, ensure_ascii=False) for row in rows))
        + ")"
        for table, rows in tables.items()
        if rows
    ]


def route_check_statements(registry: Registry) -> list[str]:
    """Return one no-row query per route and stray predicate so ClickHouse validates it."""
    predicates = {(str(r["source"]), str(r["match"])) for r in [*registry.routes, *registry.strays]}
    return [
        f"SELECT countIf({match}) FROM {DATABASE}.{source} WHERE 0 FORMAT Null"
        for source, match in sorted(predicates)
    ]


def install_statements() -> list[str]:
    """Return the ordered idempotent statements that create the whole facts store.

    Loaders, snapshots, and registry tables hold no source-of-truth state, so they
    are recreated to pick up projection, view, and registry changes.
    """
    registry = load_registry()
    return [
        _sql("001_tables.sql"),
        _sql("002_registry.sql"),
        *(f"DROP VIEW IF EXISTS {DATABASE}.refresh_{snapshot.view}" for snapshot in SNAPSHOTS),
        *(f"DROP VIEW IF EXISTS {DATABASE}.{loader.name}" for loader in LOADERS),
        *(loader_ddl(loader) for loader in LOADERS),
        _sql("003_views.sql"),
        *(statement for snapshot in SNAPSHOTS for statement in snapshot_statements(snapshot)),
        *route_check_statements(registry),
        *registry_statements(registry),
    ]


def backfill_statements(target: str, start: datetime, end: datetime) -> list[str]:
    """Return one INSERT per UTC day covering ``[start, end)`` for ``target``."""
    statements = []
    day = start
    while day < end:
        upper = min(day + timedelta(days=1), end)
        window = _BACKFILL_WINDOWS[target].format(
            start=day.strftime("%Y-%m-%d %H:%M:%S"), end=upper.strftime("%Y-%m-%d %H:%M:%S")
        )
        statements.append(
            f"INSERT INTO {DATABASE}.{target}\n{render_select(_SELECT_FILES[target], window)}"
        )
        day = upper
    return statements


def docker_runner(config: AppConfig) -> SqlRunner:
    """Return a runner that executes multi-statement SQL through ``clickhouse-client``."""
    prefix = (
        "docker",
        "--context",
        config.signoz.docker_context,
        "exec",
        "-i",
        config.signoz.clickhouse_container,
        "clickhouse-client",
        "--multiquery",
    )

    def run(sql: str) -> str:
        try:
            completed = subprocess.run(
                prefix,
                input=sql,
                text=True,
                capture_output=True,
                check=False,
                timeout=_QUERY_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise SourceError("ClickHouse statement timed out") from exc
        if completed.returncode != 0:
            raise SourceError(completed.stderr.strip().splitlines()[-1])
        return completed.stdout

    return run


def install(run: SqlRunner) -> dict[str, Any]:
    """Create the facts database, loaders, views, snapshots, and registry tables."""
    for statement in install_statements():
        run(statement)
    registry = load_registry()
    return {
        "database": DATABASE,
        "loaders": [loader.name for loader in LOADERS],
        "snapshots": [f"{snapshot.view}_snapshot" for snapshot in SNAPSHOTS],
        "registry": {"signals": len(registry.signals), "support": len(registry.support)},
    }


def backfill(run: SqlRunner, *, days: int, now: datetime | None = None) -> dict[str, Any]:
    """Copy every retained source row from the last ``days`` days into the facts tables."""
    end = now or datetime.now(UTC)
    start = (end - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    counts = {}
    for target in ("spans", "logs"):
        for statement in backfill_statements(target, start, end):
            run(statement)
        counts[target] = int(run(f"SELECT count() FROM {DATABASE}.{target} FINAL").strip())
    return {"start": start.isoformat(), "end": end.isoformat(), "rows": counts}


def status(run: SqlRunner) -> dict[str, Any]:
    """Report loader refresh state and per-harness freshness."""
    refreshes = run(
        "SELECT view, status, last_success_time, last_refresh_time, exception, "
        "written_rows FROM system.view_refreshes "
        f"WHERE database = '{DATABASE}' ORDER BY view FORMAT JSONEachRow"
    )
    freshness = run(
        "SELECT source, harness, count() AS rows, max(ts) AS latest FROM ("
        f"SELECT 'spans' AS source, harness, ts FROM {DATABASE}.spans FINAL "
        f"UNION ALL SELECT 'logs' AS source, harness, ts FROM {DATABASE}.logs FINAL"
        ") GROUP BY source, harness ORDER BY source, harness FORMAT JSONEachRow"
    )
    return {
        "loaders": [json.loads(line) for line in refreshes.splitlines() if line],
        "freshness": [json.loads(line) for line in freshness.splitlines() if line],
    }

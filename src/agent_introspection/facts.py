"""ClickHouse-materialized producer facts for the dashboard.

Refreshable materialized views copy curated producer spans and logs from the
SigNoz tables into the durable ``introspection`` database every minute. Fact
views normalize them, snapshot tables hold the last 90 days of each view's result
refreshed after every load, and the signal support registry records how each producer reaches
each dashboard signal. The dashboard reads small aggregates from the snapshots.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from typing import Any, cast

from agent_introspection.config import AppConfig, ConfigurationError, SigNozConfig

DATABASE = "introspection"
_QUERY_TIMEOUT_SECONDS = 600.0

type SqlRunner = Callable[[str], str]


class FactsError(RuntimeError):
    """A ClickHouse statement for the facts store failed."""


@dataclass(frozen=True)
class Loader:
    """One refreshable materialized view that appends a sliding source window."""

    name: str
    target: str
    select_file: str
    schedule: str
    window: str


# Spans are exported when they end, and turns can run for more than a day, so the
# minute loader selects spans that ended recently. Logs carry an ingest time, which
# can trail the event time by days, so their loader keys on `observed_timestamp`.
# Rows can still reach SigNoz's tables after the minute loaders' 30-minute window
# (a delayed collector export keeps its original observed time), so an hourly sweep
# re-reads three days of each source. The span sweep keys on end time, bounded only
# by retention, so a span that ran for days is still loaded once it is exported.
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
        window=(
            "timestamp > now() - INTERVAL 90 DAY"
            " AND timestamp + toIntervalNanosecond(duration_nano) > now() - INTERVAL 3 DAY"
        ),
    ),
    Loader(
        name="sweep_logs",
        target="logs",
        select_file="select_logs.sql",
        schedule="EVERY 1 HOUR",
        window="timestamp > toUInt64(toUnixTimestamp(now() - INTERVAL 3 DAY)) * 1000000000",
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


# Snapshots serve windows that start within the last 90 days (SigNoz's retention),
# so each refresh rebuilds a bounded window however long the durable history grows.
# They keep one extra day so a window starting exactly 90 days ago is fully covered.
# The dashboard reads the live views, which cover all history, for earlier windows.
SNAPSHOT_DAYS = 90
_SNAPSHOT_BOUND = f"ts >= now64(9) - INTERVAL {SNAPSHOT_DAYS + 1} DAY"
SNAPSHOT_FILTERS = (
    f"additional_table_filters = {{'{DATABASE}.spans': '{_SNAPSHOT_BOUND}', "
    f"'{DATABASE}.logs': '{_SNAPSHOT_BOUND}'}}"
)


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
    Snapshot("task_labels", "(harness, start_ts)"),
)

REGISTRY_FILE = "signal_support.toml"
_ALIGNMENTS = ("aligned", "differs", "not applicable")
_HARNESS_GROUPS = {"codex": ("codex-app-server", "codex_cli_rs", "codex_exec")}
# Route predicates run against the table (or, for activity hooks, the view that
# resolves each hook event's harness) named for their source.
SOURCE_TABLES = {"spans": "spans", "logs": "logs", "hooks": "hook_rows"}
_SOURCES = tuple(SOURCE_TABLES)

type Row = dict[str, str | int]


class RegistryError(ValueError):
    """The signal support registry is inconsistent."""


@dataclass(frozen=True)
class Registry:
    """Rows for the five registry tables, in file order."""

    signals: list[Row]
    routes: list[Row]
    support: list[Row]
    strays: list[Row]
    excluded: list[Row]


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
    content right after new rows land. ``additional_table_filters`` bounds every
    span and log read inside the view to the snapshot horizon, leaving the view
    SQL unchanged. Non-append refreshes swap atomically. Refreshers run with two
    threads so dashboard queries keep the CPU.
    """
    table = f"{DATABASE}.{snapshot.view}_snapshot"
    return [
        f"DROP VIEW IF EXISTS {DATABASE}.refresh_{snapshot.view}",
        f"DROP TABLE IF EXISTS {table}",
        f"CREATE TABLE {table} ENGINE = MergeTree ORDER BY {snapshot.order_by}"
        f" EMPTY AS SELECT * FROM {DATABASE}.{snapshot.view}",
        f"CREATE MATERIALIZED VIEW {DATABASE}.refresh_{snapshot.view}\n"
        f"REFRESH EVERY 1 MINUTE DEPENDS ON {DATABASE}.load_spans, {DATABASE}.load_logs\n"
        f"TO {table}\nAS SELECT * FROM {DATABASE}.{snapshot.view}\n"
        f"SETTINGS max_threads = 2, {SNAPSHOT_FILTERS}",
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
        _require(source in _SOURCES, f"{route}: source must be one of {', '.join(_SOURCES)}")
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
        _require(source in _SOURCES, f"{stray}: source must be one of {', '.join(_SOURCES)}")
        rows.append({
            "stray": stray,
            "harness": harness,
            "source": source,
            "match": _text(entry, "match", stray),
            "reason": _text(entry, "reason", stray),
        })
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
        _require(
            alignment != "not emitted",
            f"{signal}/{harness}: not emitted breaks harness parity; close the gap with a "
            "route (native or activity hook) or move the signal to [[excluded]]",
        )
        _require(alignment in _ALIGNMENTS, f"{signal}/{harness}: unknown alignment")
        route = value.get("route", "")
        note = value.get("note", "")
        if alignment == "not applicable":
            _require(route == "", f"{signal}/{harness}: not applicable has no route")
        else:
            _require(harness in covered_by.get(route, []), f"{signal}/{harness}: bad route")
        _require(
            alignment == "aligned" or note != "", f"{signal}/{harness}: {alignment} needs a note"
        )
        rows.append({
            "signal": signal,
            "harness": harness,
            "harness_label": label,
            "route": route,
            "unit": unit,
            "alignment": alignment,
            "note": note,
        })
    return rows


def _parse_excluded(
    entries: list[dict[str, Any]], harnesses: dict[str, str], views: dict[str, str]
) -> list[Row]:
    rows: list[Row] = []
    for entry in entries:
        signal = _text(entry, "id", "excluded")
        view = _text(entry, "view", signal)
        _require(view in views, f"{signal}: unknown view {view}")
        missing = cast(list[str], entry.get("missing", []))
        _require(bool(missing) and set(missing) <= set(harnesses), f"{signal}: unknown harness")
        rows.append({
            "signal": signal,
            "view": view,
            "title": _text(entry, "title", signal),
            "missing": ", ".join(harnesses[harness] for harness in missing),
            "reason": _text(entry, "reason", signal),
        })
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
        signals.append({
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
        })
        declared = cast(dict[str, dict[str, str]], entry.get("support", {}))
        if scope == "system":
            _require(not declared, f"{signal}: system signals have no per-harness support")
        else:
            support.extend(_parse_support(signal, unit, declared, harnesses, covered_by))
    excluded = _parse_excluded(document.get("excluded", []), harnesses, views)
    listed = {row["signal"] for row in signals}
    _require(
        not listed & {row["signal"] for row in excluded},
        "a signal cannot be both listed and excluded",
    )
    return Registry(
        signals=signals,
        routes=routes,
        support=support,
        strays=_parse_strays(document.get("stray", []), harnesses),
        excluded=excluded,
    )


def load_registry() -> Registry:
    """Parse and validate the repo-owned signal support registry."""
    return parse_registry(tomllib.loads(_sql(REGISTRY_FILE)))


def sql_string(value: str) -> str:
    """Quote a value as a ClickHouse string literal."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def registry_statements(registry: Registry) -> list[str]:
    """Return statements that replace the registry tables with the file's rows."""
    tables: dict[str, list[Row]] = {
        "signals": registry.signals,
        "signal_routes": registry.routes,
        "signal_support": registry.support,
        "signal_strays": registry.strays,
        "signal_exclusions": registry.excluded,
    }
    return [
        f"INSERT INTO {DATABASE}.{table} SELECT * FROM format(JSONEachRow, "
        + sql_string("\n".join(json.dumps(row, ensure_ascii=False) for row in rows))
        + ")"
        for table, rows in tables.items()
        if rows
    ]


def route_check_statements(registry: Registry) -> list[str]:
    """Return one no-row query per route and stray predicate so ClickHouse validates it."""
    predicates = {(str(r["source"]), str(r["match"])) for r in [*registry.routes, *registry.strays]}
    return [
        f"SELECT countIf({match}) FROM {DATABASE}.{SOURCE_TABLES[source]} WHERE 0 FORMAT Null"
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
    # Without a configured context, docker uses the current one (`docker context show`).
    context = config.signoz.docker_context
    prefix = (
        "docker",
        *(("--context", context) if context is not None else ()),
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
            raise FactsError("ClickHouse statement timed out") from exc
        if completed.returncode != 0:
            lines = completed.stderr.strip().splitlines()
            raise FactsError(
                next(
                    (line for line in lines if "DB::Exception" in line), lines[-1] if lines else ""
                )
            )
        return completed.stdout

    return run


_QUOTES = frozenset("'\"`")


def _skip_quoted(sql: str, start: int) -> int:
    """Return the index just past the literal or identifier opened at ``start``."""
    quote = sql[start]
    index = start + 1
    while index < len(sql):
        char = sql[index]
        # A backslash escape or a doubled quote both skip two characters.
        if char == "\\" or (char == quote and sql[index + 1 : index + 2] == quote):
            index += 2
        elif char == quote:
            return index + 1
        else:
            index += 1
    return index


def _skip_comment(sql: str, start: int) -> int:
    """Return the index just past a comment at ``start``, or ``start`` if none."""
    if sql.startswith("--", start):
        end = sql.find("\n", start)
        return len(sql) if end < 0 else end + 1
    if sql.startswith("/*", start):
        end = sql.find("*/", start + 2)
        return len(sql) if end < 0 else end + 2
    return start


def split_statements(sql: str) -> list[str]:
    """Split multi-statement SQL on top-level ``;``.

    Semicolons inside string literals, quoted identifiers, and comments do not split.
    Pieces holding only whitespace and comments are dropped.
    """
    statements: list[str] = []
    start = index = 0
    has_code = False
    while index < len(sql):
        char = sql[index]
        skipped = _skip_comment(sql, index)
        if skipped != index:
            index = skipped
            continue
        if char == ";":
            if has_code:
                statements.append(sql[start:index].strip())
            start, index, has_code = index + 1, index + 1, False
            continue
        has_code = has_code or not char.isspace()
        index = _skip_quoted(sql, index) if char in _QUOTES else index + 1
    if has_code:
        statements.append(sql[start:].strip())
    return statements


def _exception_line(text: str) -> str:
    lines = text.strip().splitlines()
    return next((line for line in lines if "DB::Exception" in line), lines[-1] if lines else "")


def _run_password_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)


def clickhouse_password(signoz: SigNozConfig) -> str:
    """Return the HTTP-mode password from its environment variable or command.

    Neither the value nor the command's output is ever logged or put in an error.
    """
    if signoz.clickhouse_password_env is not None:
        if not os.environ.get(signoz.clickhouse_password_env):
            raise ConfigurationError(
                f"environment variable {signoz.clickhouse_password_env} "
                "(signoz.clickhouse_password_env) is not set"
            )
        return os.environ[signoz.clickhouse_password_env]
    if signoz.clickhouse_password_command is not None:
        try:
            result = _run_password_command(signoz.clickhouse_password_command)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ConfigurationError("signoz.clickhouse_password_command could not run") from exc
        password = result.stdout.strip()
        if result.returncode != 0 or not password:
            raise ConfigurationError(
                f"signoz.clickhouse_password_command printed no password (exit {result.returncode})"
            )
        return password
    return ""


def http_runner(config: AppConfig) -> SqlRunner:
    """Return a runner that executes SQL over the ClickHouse HTTP interface.

    Each statement goes in its own POST, because the HTTP interface runs one statement
    per request, and outputs are concatenated like ``clickhouse-client --multiquery``.
    The password comes from the environment variable the config names; it is never
    logged or included in errors.
    """
    signoz = config.signoz
    if signoz.clickhouse_url is None:
        raise ConfigurationError("signoz.clickhouse_url is not configured")
    password = clickhouse_password(signoz)
    token = base64.b64encode(f"{signoz.clickhouse_user}:{password}".encode()).decode()
    # wait_end_of_query buffers the result, so a failure mid-query is an HTTP error.
    url = f"{signoz.clickhouse_url}/?wait_end_of_query=1"

    def execute(statement: str) -> str:
        request = urllib.request.Request(
            url,
            data=statement.encode(),
            method="POST",
            headers={"Authorization": f"Basic {token}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=_QUERY_TIMEOUT_SECONDS) as response:
                return cast(bytes, response.read()).decode()
        except urllib.error.HTTPError as exc:
            raise FactsError(_exception_line(exc.read().decode(errors="replace"))) from None
        except TimeoutError as exc:
            raise FactsError("ClickHouse statement timed out") from exc
        except urllib.error.URLError as exc:
            raise FactsError(f"ClickHouse HTTP request failed: {exc.reason}") from None

    def run(sql: str) -> str:
        return "".join(execute(statement) for statement in split_statements(sql))

    return run


def runner(config: AppConfig) -> SqlRunner:
    """Return the SQL runner for the configured connection mode."""
    return http_runner(config) if config.signoz.mode == "http" else docker_runner(config)


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

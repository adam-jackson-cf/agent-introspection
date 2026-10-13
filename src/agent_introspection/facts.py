"""ClickHouse-materialized producer facts for the dashboard.

Refreshable materialized views copy curated producer spans and logs from the
SigNoz tables into the durable ``introspection`` database every minute. Fact
views normalize them, snapshot tables hold the last 90 days of each view's result
rebuilt every 10 minutes after a load, and the signal support registry records how
each producer reaches each dashboard signal. The dashboard reads small aggregates
from the snapshots.

Refreshers are staggered across the hour so no two heavy queries overlap, because
the server kills a query when total memory passes its limit. Minute map, in UTC:
``:x0`` minute loaders only; ``:x1``-``:x4`` and ``:x6``-``:x8`` one recent
refresher each; ``:x5`` one history refresher (00:35-01:35); ``:09``, ``:19``,
``:29`` and ``:39`` one hourly sweep each. Every refresher is created ``EMPTY`` so
creating it runs nothing; ``install`` then refreshes the minute loaders and snapshots
one at a time, retrying a failed one once; scheduled retries back off per refresher class.
"""

from __future__ import annotations

import base64
import itertools
import json
import os
import re
import subprocess
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from typing import Any, cast

from agent_introspection import command_shape
from agent_introspection.config import AppConfig, ConfigurationError, SigNozConfig, is_local_host

DATABASE = "introspection"
# `facts verify` builds a disposable copy of the store in a database of its own per run
# (`<prefix>_<unix time>_<pid>`), so concurrent runs never drop each other's copy.
VERIFY_PREFIX = f"{DATABASE}_verify"
_VERIFY_STALE_SECONDS = 3600
_VERIFY_NAME = re.compile(rf"^{VERIFY_PREFIX}_(\d+)_\d+$")
_DATABASE_REFERENCE = re.compile(rf"\b{DATABASE}(?=[.;])")
_REFRESH_SCHEDULE = re.compile(
    r"REFRESH EVERY \d+ (?:MINUTE|HOUR|DAY)((?: OFFSET \d+ (?:MINUTE|HOUR))?)"
)
_QUERY_TIMEOUT_SECONDS = 600.0

type SqlRunner = Callable[[str], str]


class FactsError(RuntimeError):
    """A ClickHouse statement for the facts store failed."""


class RefreshStillRunningError(FactsError):
    """A refresh outlived its wait budget; the server is still running it."""


class StatementTimeoutError(FactsError):
    """A statement outlived the runner timeout; ClickHouse may still be running it."""


@dataclass(frozen=True)
class Loader:
    """One refreshable materialized view that appends a sliding source window."""

    name: str
    target: str
    select_file: str
    schedule: str
    window: str


# Hook-event and session-attribution records come from spool exporters on every
# machine, so their loaders key on `observed_timestamp` (the collector's receive time)
# alone: a record's own time is the producer's clock and can trail by an outage.
_RECORD_LOAD_WINDOW = (
    "observed_timestamp > toUInt64(toUnixTimestamp(now() - INTERVAL 30 MINUTE)) * 1000000000"
)
_RECORD_SWEEP_WINDOW = (
    "observed_timestamp > toUInt64(toUnixTimestamp(now() - INTERVAL 3 DAY)) * 1000000000"
)
_RECORD_BACKFILL_WINDOW = (
    "observed_timestamp >= toUInt64(toUnixTimestamp64Nano(toDateTime64('{start}', 9, 'UTC')))"
    " AND observed_timestamp < toUInt64(toUnixTimestamp64Nano(toDateTime64('{end}', 9, 'UTC')))"
)

# Spans are exported when they end, and turns can run for more than a day, so the
# minute loader selects spans that ended recently. Logs carry an ingest time, which
# can trail the event time by days, so their loader keys on `observed_timestamp`.
# Rows can still reach SigNoz's tables after the minute loaders' 30-minute window
# (a delayed collector export keeps its original observed time), so an hourly sweep
# re-reads three days of each source. The span sweep keys on end time, bounded only
# by retention, so a span that ran for days is still loaded once it is exported.
MINUTE_SCHEDULE = "EVERY 1 MINUTE"


def sweep_schedule(minute: int) -> str:
    """Return the hourly schedule of a sweep; each sweep owns one :x9 minute."""
    return f"EVERY 1 HOUR OFFSET {minute} MINUTE"


LOADERS = (
    Loader(
        name="load_spans",
        target="spans",
        select_file="select_spans.sql",
        schedule=MINUTE_SCHEDULE,
        window=(
            "timestamp > now() - INTERVAL 1 DAY"
            " AND timestamp + toIntervalNanosecond(duration_nano) > now() - INTERVAL 30 MINUTE"
        ),
    ),
    Loader(
        name="sweep_spans",
        target="spans",
        select_file="select_spans.sql",
        schedule=sweep_schedule(9),
        window=(
            "timestamp > now() - INTERVAL 90 DAY"
            " AND timestamp + toIntervalNanosecond(duration_nano) > now() - INTERVAL 3 DAY"
        ),
    ),
    Loader(
        name="sweep_logs",
        target="logs",
        select_file="select_logs.sql",
        schedule=sweep_schedule(19),
        window="timestamp > toUInt64(toUnixTimestamp(now() - INTERVAL 3 DAY)) * 1000000000",
    ),
    Loader(
        name="load_logs",
        target="logs",
        select_file="select_logs.sql",
        schedule=MINUTE_SCHEDULE,
        window=(
            "observed_timestamp"
            " > toUInt64(toUnixTimestamp(now() - INTERVAL 30 MINUTE)) * 1000000000"
            " AND timestamp > toUInt64(toUnixTimestamp(now() - INTERVAL 7 DAY)) * 1000000000"
        ),
    ),
    Loader(
        name="sweep_hook_events",
        target="hook_events",
        select_file="select_hook_events.sql",
        schedule=sweep_schedule(29),
        window=_RECORD_SWEEP_WINDOW,
    ),
    Loader(
        name="load_hook_events",
        target="hook_events",
        select_file="select_hook_events.sql",
        schedule=MINUTE_SCHEDULE,
        window=_RECORD_LOAD_WINDOW,
    ),
    Loader(
        name="sweep_session_projects",
        target="session_projects",
        select_file="select_session_projects.sql",
        schedule=sweep_schedule(39),
        window=_RECORD_SWEEP_WINDOW,
    ),
    Loader(
        name="load_session_projects",
        target="session_projects",
        select_file="select_session_projects.sql",
        schedule=MINUTE_SCHEDULE,
        window=_RECORD_LOAD_WINDOW,
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
    "hook_events": _RECORD_BACKFILL_WINDOW,
    "session_projects": _RECORD_BACKFILL_WINDOW,
}
_SELECT_FILES = {
    "spans": "select_spans.sql",
    "logs": "select_logs.sql",
    "hook_events": "select_hook_events.sql",
    "session_projects": "select_session_projects.sql",
}


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
# The SigNoz ClickHouse container runs near its ~4 GiB memory cap, but the default
# profile lets one query use 10 GB, so a heavy refresh is killed (Code 241) instead of
# slowing down. Each refresher gets a realistic per-query ceiling, and grouping, sorting
# and joining spill to disk at half of it, so it finishes slower rather than failing.
REFRESH_MAX_MEMORY = 1536 * 1024 * 1024
REFRESH_SPILL_BYTES = REFRESH_MAX_MEMORY // 2
REFRESH_SETTINGS = (
    "max_threads = 2, "
    f"max_memory_usage = {REFRESH_MAX_MEMORY}, "
    f"max_bytes_before_external_group_by = {REFRESH_SPILL_BYTES}, "
    f"max_bytes_before_external_sort = {REFRESH_SPILL_BYTES}, "
    "join_algorithm = 'grace_hash,hash'"
)
# Loaders, sweeps and backfill INSERT ... SELECT run the heavy projections over raw
# source rows, so they use the refresh ceiling plus small blocks and one insert thread.
# Backfill runs over short windows; rows are identical to a whole-day run, and
# ReplacingMergeTree makes a rerun idempotent. Do not cap below 1.5 GiB: the log sweep
# peaks near 1.05 GiB.
BACKFILL_WINDOW = timedelta(hours=6)
LOAD_SETTINGS = f"{REFRESH_SETTINGS}, max_block_size = 8192, max_insert_threads = 1"
# Each snapshot is two tables behind one view. Recent holds the days whose facts can
# still change and is rebuilt every 10 minutes: the minute log loader accepts a log up
# to 7 days after its event (it selects by arrival time), so recent covers 8 days and
# reads one more day of spans and logs as context for rows near its start (a task's
# next task, a call's turn). History holds the rest and is rebuilt once a day. The
# view takes rows before the boundary from history and the rest from recent; both
# tables overlap the boundary, so between rebuilds no row is missed or repeated:
# history built yesterday still reaches the boundary, recent always starts before it.
RECENT_PERIOD_MINUTES = 10
# Minute map within each 10-minute period (see the module docstring): :x0 is left to the
# minute loaders, :x5 to history, and the sweeps run in the :x9 class, so recents take
# the rest. task_labels follows task_outcomes by one minute, which it waits for.
RECENT_OFFSET_MINUTES = {
    "usage_events": 1,
    "task_outcomes": 2,
    "task_labels": 3,
    "tool_calls": 4,
    "user_signals": 6,
    "model_calls": 7,
    "model_call_attempts": 8,
}
# Each history refresher scans the most data, and the server kills a query when total
# server RSS (not one query's memory) passes its limit. So history refreshers run one at
# a time, 10 minutes apart from 00:35 UTC, always on a :x5 minute: the minute loaders own
# :x0, the recent refreshers :x1-:x4 and :x6-:x8, and the hourly sweeps :x9. Offsets are minute-only
# because `_REFRESH_SCHEDULE` rewrites a single-unit OFFSET for `facts verify`. A view
# that DEPENDS ON another must come later in SNAPSHOTS, for recent and history alike:
# ClickHouse waits only for a dependency whose offset is earlier in the same period.
HISTORY_FIRST_OFFSET_MINUTES = 35
HISTORY_OFFSET_STEP_MINUTES = 10
# Retry backoff counts from the END of the failed attempt, not its start, and the
# server does not retry a refresh started by `SYSTEM REFRESH VIEW` (install retries
# those once itself). A retry therefore lands wherever the attempt ended plus the
# backoff, so each refresher class picks a backoff that clears the slots it could hit:
# - minute loaders retry within a minute or two; the next scheduled run is a minute away
#   and the loaders are light, so a short backoff recovers quickly from a memory burst;
# - recents rebuild every 10 minutes anyway, so they keep a 10-minute backoff;
# - sweeps run hourly, so one retry after a full hour follows the sweep's own next slot
#   instead of landing on a neighbouring sweep 10 minutes later;
# - histories run one a day at :x5, ten minutes apart. Only they are isolated from one
#   another: a 70-minute backoff clears the whole 00:35-01:35 block and stays in :x5.
REFRESH_RETRY_BACKOFF_MS = RECENT_PERIOD_MINUTES * 60_000
HISTORY_RETRY_BACKOFF_MS = 70 * 60_000
SWEEP_RETRY_BACKOFF_MS = 60 * 60_000
LOADER_RETRY_INITIAL_BACKOFF_MS = 30_000
LOADER_RETRY_MAX_BACKOFF_MS = 60_000


def refresh_retry_settings(
    retries: int, initial_ms: int = REFRESH_RETRY_BACKOFF_MS, max_ms: int | None = None
) -> str:
    """Return the retry settings for ``retries`` retries, backing off from ``initial_ms``."""
    return (
        f"refresh_retries = {retries}, "
        f"refresh_retry_initial_backoff_ms = {initial_ms}, "
        f"refresh_retry_max_backoff_ms = {initial_ms if max_ms is None else max_ms}"
    )


LOADER_RETRY_SETTINGS = refresh_retry_settings(
    3, LOADER_RETRY_INITIAL_BACKOFF_MS, LOADER_RETRY_MAX_BACKOFF_MS
)
SWEEP_RETRY_SETTINGS = refresh_retry_settings(1, SWEEP_RETRY_BACKOFF_MS)
RECENT_RETRY_SETTINGS = refresh_retry_settings(2)
HISTORY_RETRY_SETTINGS = refresh_retry_settings(3, HISTORY_RETRY_BACKOFF_MS)
HISTORY_END_DAYS = 6
BOUNDARY_DAYS = 7
RECENT_START_DAYS = 8
RECENT_CONTEXT_DAYS = 1


def _days_before_today(days: int) -> str:
    return f"toStartOfDay(now64(9), 'UTC') - INTERVAL {days} DAY"


# Quoted inside the settings map, so its own quotes are escaped.
_RECENT_BOUND = f"ts >= {_days_before_today(RECENT_START_DAYS + RECENT_CONTEXT_DAYS)}".replace(
    "'", "\\'"
)
RECENT_FILTERS = (
    f"additional_table_filters = {{'{DATABASE}.spans': '{_RECENT_BOUND}', "
    f"'{DATABASE}.logs': '{_RECENT_BOUND}'}}"
)


@dataclass(frozen=True)
class Snapshot:
    """One fact view's result, split into history and recent tables behind one view.

    The tables are ``<view>_history`` and ``<view>_recent``; readers use ``<view>_snapshot``.

    ``time_column`` is the row time the boundary splits on. ``views_file`` is the SQL
    file defining the view; install creates each file's snapshots right after it,
    because later views read earlier snapshots. ``reads`` names the snapshot this
    view reads, whose refreshes each part waits for.
    """

    view: str
    order_by: str
    time_column: str = "ts"
    views_file: str = "003_views.sql"
    reads: str = ""


# task_labels reads task_outcomes_snapshot, so its view lives in a later file.
VIEW_FILES = ("003_views.sql", "004_labels.sql")
SNAPSHOTS = (
    Snapshot("usage_events", "(harness, ts)"),
    Snapshot("task_outcomes", "(harness, start_ts)", "start_ts"),
    Snapshot("tool_calls", "(harness, ts)"),
    Snapshot("user_signals", "(harness, ts)"),
    Snapshot("model_calls", "(harness, ts)"),
    Snapshot("model_call_attempts", "(harness, ts)"),
    Snapshot("task_labels", "(harness, start_ts)", "start_ts", "004_labels.sql", "task_outcomes"),
)
assert set(RECENT_OFFSET_MINUTES) == {s.view for s in SNAPSHOTS}, "one recent minute per snapshot"


def history_schedule(snapshot: Snapshot) -> str:
    """Return the daily schedule of a snapshot's history refresher, staggered by position."""
    offset = HISTORY_FIRST_OFFSET_MINUTES + HISTORY_OFFSET_STEP_MINUTES * SNAPSHOTS.index(snapshot)
    return f"EVERY 1 DAY OFFSET {offset} MINUTE"


def recent_schedule(snapshot: Snapshot) -> str:
    """Return the schedule of a snapshot's recent refresher, at its own minute."""
    offset = RECENT_OFFSET_MINUTES[snapshot.view]
    return f"EVERY {RECENT_PERIOD_MINUTES} MINUTE OFFSET {offset} MINUTE"


REGISTRY_FILE = "signal_support.toml"
_ALIGNMENTS = ("aligned", "differs", "not applicable", "not emitted")
# Alignments that reach the signal on a machine using the harness; `not emitted`
# hides the signal wherever the harness is enabled.
REACHES = frozenset({"aligned", "differs", "not applicable"})
_ROUTELESS = frozenset({"not applicable", "not emitted"})
_HARNESS_GROUPS = {"codex": ("codex-app-server", "codex_cli_rs", "codex_exec")}
# Detectors and evidence count only the harnesses this machine uses
# (`introspection.harnesses`, loaded by `facts install` from the config).
ENABLED_HARNESSES = f"(SELECT harness FROM {DATABASE}.harnesses WHERE enabled = 1)"


def enabled_filter(column: str = "harness") -> str:
    """Return a predicate keeping rows of the enabled harnesses."""
    return f"{column} IN {ENABLED_HARNESSES}"


# Route predicates run against the table (or, for activity hooks, the view that
# resolves each hook event's harness) named for their source.
SOURCE_TABLES = {"spans": "spans", "logs": "logs", "hooks": "hook_rows"}
_SOURCES = tuple(SOURCE_TABLES)

type Row = dict[str, str | int]


class RegistryError(ValueError):
    """The signal support registry is inconsistent."""


@dataclass(frozen=True)
class Registry:
    """Rows for the five registry tables, in file order, and the harness labels."""

    signals: list[Row]
    routes: list[Row]
    support: list[Row]
    strays: list[Row]
    excluded: list[Row]
    harnesses: dict[str, str] = field(default_factory=dict)


def _sql(name: str) -> str:
    return files("agent_introspection.facts_sql").joinpath(name).read_text()


def _command_shape_fragment() -> str:
    """Return the command-shape SQL fragment with its rules rendered from Python."""
    return command_shape.fill(_sql("command_shape.sql"), command_shape.sql_parts())


def render_select(select_file: str, window: str) -> str:
    """Return a curated projection restricted to one source-time predicate."""
    select = _sql(select_file)
    if "{" + "command_shape}" in select:
        select = command_shape.fill(
            select, {"command_shape": _command_shape_fragment().rstrip("\n")}
        )
    return command_shape.fill(select, {"window": window})


def loader_ddl(loader: Loader) -> str:
    """Return the refreshable materialized view definition for one loader.

    ``EMPTY`` keeps creation from running the loader. ``install`` refreshes the minute
    loaders itself, one at a time; a sweep waits for its own hourly slot, because the
    minute loaders already cover the recent window and the sweep only catches late rows.
    """
    retry = LOADER_RETRY_SETTINGS if loader.schedule == MINUTE_SCHEDULE else SWEEP_RETRY_SETTINGS
    return (
        f"CREATE MATERIALIZED VIEW {DATABASE}.{loader.name}\n"
        f"REFRESH {loader.schedule}\n"
        f"SETTINGS {retry}\n"
        f"APPEND TO {DATABASE}.{loader.target} EMPTY\n"
        f"AS\n{render_select(loader.select_file, loader.window).rstrip()}\n"
        f"SETTINGS {LOAD_SETTINGS}"
    )


def snapshot_refreshers(snapshot: Snapshot) -> tuple[str, str]:
    """Return the names of the history and recent refreshers of one snapshot."""
    return (f"refresh_{snapshot.view}_history", f"refresh_{snapshot.view}_recent")


def snapshot_statements(snapshot: Snapshot) -> list[str]:
    """Return statements that recreate one snapshot's two tables, refreshers and view.

    Each refresher replaces its whole table (non-append refreshes swap atomically),
    so neither table ever holds duplicates or rows the facts no longer produce. Both
    wait for the minute loaders, and for the refreshes of a snapshot the view reads.
    ``additional_table_filters`` bounds the spans and logs each part reads, leaving
    the view SQL unchanged. Refreshers run with two threads so dashboard queries keep
    the CPU, and with a memory ceiling below the server's that spills to disk
    (``REFRESH_SETTINGS``). Refreshers are created ``EMPTY``: creation fires none, and
    ``install`` refreshes them one at a time. The pre-split single-table refresher and
    table are dropped too.
    """
    base = f"{DATABASE}.{snapshot.view}"
    column = snapshot.time_column
    boundary = _days_before_today(BOUNDARY_DAYS)
    history, recent = (f"{base}_history", f"{base}_recent")

    def refresher(part: int, schedule: str, table: str, keep: str, filters: str) -> str:
        minute_loaders = (loader for loader in LOADERS if loader.schedule == MINUTE_SCHEDULE)
        waits = [f"{DATABASE}.{loader.name}" for loader in minute_loaders]
        if snapshot.reads:
            upstream = next(s for s in SNAPSHOTS if s.view == snapshot.reads)
            waits.append(f"{DATABASE}.{snapshot_refreshers(upstream)[part]}")
        return (
            f"CREATE MATERIALIZED VIEW {DATABASE}.{snapshot_refreshers(snapshot)[part]}\n"
            f"REFRESH {schedule} DEPENDS ON {', '.join(waits)}\n"
            f"SETTINGS {(HISTORY_RETRY_SETTINGS, RECENT_RETRY_SETTINGS)[part]}\n"
            f"TO {table} EMPTY\nAS SELECT * FROM {base} WHERE {keep}\n"
            f"SETTINGS {REFRESH_SETTINGS}, {filters}"
        )

    return [
        f"DROP VIEW IF EXISTS {DATABASE}.refresh_{snapshot.view}",
        *(f"DROP VIEW IF EXISTS {DATABASE}.{name}" for name in snapshot_refreshers(snapshot)),
        f"DROP TABLE IF EXISTS {base}_snapshot",
        *(f"DROP TABLE IF EXISTS {table}" for table in (history, recent)),
        *(
            f"CREATE TABLE {table} ENGINE = MergeTree ORDER BY {snapshot.order_by}"
            f" EMPTY AS SELECT * FROM {base}"
            for table in (history, recent)
        ),
        f"CREATE VIEW {base}_snapshot AS\n"
        f"SELECT * FROM {history} WHERE {column} < {boundary}\n"
        f"UNION ALL\n"
        f"SELECT * FROM {recent} WHERE {column} >= {boundary}",
        refresher(
            0,
            history_schedule(snapshot),
            history,
            f"{column} < {_days_before_today(HISTORY_END_DAYS)}",
            SNAPSHOT_FILTERS,
        ),
        refresher(
            1,
            recent_schedule(snapshot),
            recent,
            f"{column} >= {_days_before_today(RECENT_START_DAYS)}",
            RECENT_FILTERS,
        ),
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


def _parse_strays(
    entries: list[dict[str, Any]], harnesses: dict[str, str], producers: dict[str, str]
) -> list[Row]:
    rows: list[Row] = []
    for entry in entries:
        stray = _text(entry, "id", "stray")
        harness = _text(entry, "harness", stray)
        _require(harness in harnesses or harness in producers, f"{stray}: unknown harness")
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
        _require(alignment in _ALIGNMENTS, f"{signal}/{harness}: unknown alignment")
        route = value.get("route", "")
        note = value.get("note", "")
        if alignment in _ROUTELESS:
            _require(route == "", f"{signal}/{harness}: {alignment} has no route")
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
    _require(
        any(row["route"] != "" for row in rows),
        f"{signal}: no harness emits it; list it under [[excluded]]",
    )
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
        _require(set(missing) <= set(harnesses), f"{signal}: unknown harness")
        _require(
            set(missing) == set(harnesses),
            f"{signal}: [[excluded]] is for signals no harness produces; list it as a "
            "[[signal]] with `not emitted` for the harnesses that lack it",
        )
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
        strays=_parse_strays(document.get("stray", []), harnesses, document.get("producers", {})),
        excluded=excluded,
        harnesses=dict(harnesses),
    )


def load_registry() -> Registry:
    """Parse and validate the repo-owned signal support registry."""
    return parse_registry(tomllib.loads(_sql(REGISTRY_FILE)))


def enabled_harnesses(registry: Registry, enabled: Sequence[str] | None) -> tuple[str, ...]:
    """Resolve the configured harness keys to registry harnesses, in registry order.

    None enables every registry harness; `codex` names the three Codex surfaces.
    """
    if enabled is None:
        return tuple(registry.harnesses)
    unknown = [
        key for key in enabled if key not in registry.harnesses and key not in _HARNESS_GROUPS
    ]
    if unknown:
        raise ConfigurationError(
            f"harnesses.enabled: unknown harness {', '.join(unknown)}; known: "
            + ", ".join([*registry.harnesses, *_HARNESS_GROUPS])
        )
    chosen = {harness for key in enabled for harness in _HARNESS_GROUPS.get(key, (key,))}
    return tuple(harness for harness in registry.harnesses if harness in chosen)


def harness_rows(registry: Registry, enabled: Sequence[str]) -> list[Row]:
    """Return one `introspection.harnesses` row per registry harness."""
    return [
        {"harness": harness, "label": label, "enabled": int(harness in enabled)}
        for harness, label in registry.harnesses.items()
    ]


def hidden_signals(registry: Registry, enabled: Sequence[str]) -> list[Row]:
    """Return the harness-scoped signals hidden on a machine using ``enabled``.

    A signal is visible only when every enabled harness reaches it (aligned, differs,
    or not applicable); an enabled harness that does not emit it hides it here. One
    row per signal and reason, naming the enabled harnesses it applies to.
    """
    hidden: list[Row] = []
    for signal in registry.signals:
        by_note: dict[str, list[str]] = {}
        for row in registry.support:
            if (
                row["signal"] == signal["signal"]
                and row["harness"] in enabled
                and row["alignment"] not in REACHES
            ):
                by_note.setdefault(str(row["note"]), []).append(str(row["harness_label"]))
        hidden.extend(
            {
                "signal": signal["signal"],
                "view": signal["view"],
                "title": signal["title"],
                "missing": ", ".join(labels),
                "note": note,
            }
            for note, labels in by_note.items()
        )
    return hidden


def sql_string(value: str) -> str:
    """Quote a value as a ClickHouse string literal."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def registry_statements(registry: Registry, enabled: Sequence[str]) -> list[str]:
    """Return statements that fill the registry tables and this machine's harnesses."""
    tables: dict[str, list[Row]] = {
        "harnesses": harness_rows(registry, enabled),
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


def view_file_statements(views_file: str) -> list[str]:
    """Return a view file's DDL followed by the statements of the snapshots it defines."""
    steps = [
        step
        for snapshot in SNAPSHOTS
        if snapshot.views_file == views_file
        for step in snapshot_statements(snapshot)
    ]
    return [_sql(views_file), *steps]


def install_statements(enabled: Sequence[str] | None = None) -> list[str]:
    """Return the ordered idempotent statements that create the whole facts store.

    Loaders, snapshots, and registry tables hold no source-of-truth state, so they
    are recreated to pick up projection, view, registry, and harness changes. The
    registry tables are replaced only after every route predicate has validated,
    so a failing check leaves the previous registry readable.
    ``enabled`` holds the configured harness keys; None enables every harness.
    """
    registry = load_registry()
    harnesses = enabled_harnesses(registry, enabled)
    return [
        _sql("001_tables.sql"),
        # Refreshers go before the loaders they wait on, a reader's before its source's.
        *(
            f"DROP VIEW IF EXISTS {DATABASE}.{name}"
            for snapshot in reversed(SNAPSHOTS)
            for name in (*snapshot_refreshers(snapshot), f"refresh_{snapshot.view}")
        ),
        *(f"DROP VIEW IF EXISTS {DATABASE}.{loader.name}" for loader in LOADERS),
        *(loader_ddl(loader) for loader in LOADERS),
        *(statement for views_file in VIEW_FILES for statement in view_file_statements(views_file)),
        *route_check_statements(registry),
        _sql("002_registry.sql"),
        *registry_statements(registry, harnesses),
    ]


def backfill_windows(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Split ``[start, end)`` into consecutive ``BACKFILL_WINDOW`` pieces, the last clipped."""
    windows = []
    lower = start
    while lower < end:
        upper = min(lower + BACKFILL_WINDOW, end)
        windows.append((lower, upper))
        lower = upper
    return windows


def backfill_statement(target: str, lower: datetime, upper: datetime) -> str:
    """Return one memory-bounded INSERT of the source rows in ``[lower, upper)``."""
    window = _BACKFILL_WINDOWS[target].format(
        start=lower.strftime("%Y-%m-%d %H:%M:%S"), end=upper.strftime("%Y-%m-%d %H:%M:%S")
    )
    return (
        f"INSERT INTO {DATABASE}.{target}\n{render_select(_SELECT_FILES[target], window)}\n"
        f"SETTINGS {LOAD_SETTINGS}"
    )


def backfill_statements(target: str, start: datetime, end: datetime) -> list[str]:
    """Return one INSERT per backfill window covering ``[start, end)`` for ``target``."""
    return [
        backfill_statement(target, lower, upper) for lower, upper in backfill_windows(start, end)
    ]


def _require_local_docker_context(context: str | None) -> None:
    """Raise unless the Docker context's daemon endpoint is a local socket or host."""
    command = ("docker", "context", "inspect", *((context,) if context is not None else ()))
    try:
        completed = _run_local_command(command)
        host = json.loads(completed.stdout)[0]["Endpoints"]["docker"]["Host"]
    except (OSError, subprocess.TimeoutExpired, ValueError, LookupError, TypeError) as exc:
        raise ConfigurationError("could not resolve the Docker context endpoint") from exc
    if not isinstance(host, str):
        raise ConfigurationError("could not resolve the Docker context endpoint")
    parsed = urllib.parse.urlsplit(host)
    if parsed.scheme in ("unix", "npipe") or (
        parsed.scheme == "tcp" and is_local_host(parsed.hostname or "")
    ):
        return
    raise ConfigurationError(
        "signoz.docker_context must target a Docker daemon on this machine, "
        f"not {parsed.scheme}://{parsed.hostname or ''}"
    )


def docker_runner(config: AppConfig) -> SqlRunner:
    """Return a runner that executes multi-statement SQL through ``clickhouse-client``.

    The first statement batch verifies that the Docker context targets a local daemon.
    """
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
    verified = False

    def run(sql: str) -> str:
        nonlocal verified
        if not verified:
            _require_local_docker_context(context)
            verified = True
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
            raise StatementTimeoutError("ClickHouse statement timed out") from exc
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


def _run_local_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
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
            result = _run_local_command(signoz.clickhouse_password_command)
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
            with exc:
                detail = exc.read().decode(errors="replace")
            raise FactsError(_exception_line(detail)) from None
        except TimeoutError as exc:
            raise StatementTimeoutError("ClickHouse statement timed out") from exc
        except urllib.error.URLError as exc:
            raise FactsError(f"ClickHouse HTTP request failed: {exc.reason}") from None

    def run(sql: str) -> str:
        return "".join(execute(statement) for statement in split_statements(sql))

    return run


def runner(config: AppConfig) -> SqlRunner:
    """Return the SQL runner for the configured connection mode."""
    return http_runner(config) if config.signoz.mode == "http" else docker_runner(config)


def scratch_database_name(now: float | None = None) -> str:
    """Return a scratch database name unique to this run: prefix, unix time and pid."""
    return f"{VERIFY_PREFIX}_{int(time.time() if now is None else now)}_{os.getpid()}"


def verify_statements(
    enabled: Sequence[str] | None = None, database: str | None = None
) -> list[str]:
    """Return the install statements rewritten to build a scratch copy of the store.

    Every reference to the facts database points at ``database`` (a fresh
    ``scratch_database_name`` by default) instead, and each refreshable view (already
    ``EMPTY``) gets a yearly schedule, so ClickHouse checks every statement (including
    each view's columns against its target table) without loading a row or touching
    the live store.
    """
    database = database or scratch_database_name()

    def scratch(statement: str) -> str:
        statement = _DATABASE_REFERENCE.sub(database, statement)
        if not _REFRESH_SCHEDULE.search(statement):
            return statement
        return _REFRESH_SCHEDULE.sub(r"REFRESH EVERY 1 YEAR\1", statement)

    return [
        f"DROP DATABASE IF EXISTS {database} SYNC",
        *(scratch(statement) for statement in install_statements(enabled)),
    ]


def _legacy_scratch_is_idle(run: SqlRunner, now: float) -> bool:
    """Return whether the fixed-name scratch database has no table changed within an hour.

    An older-version verify (another worktree) may be building in it right now.
    """
    output = run(
        "SELECT toUnixTimestamp(max(metadata_modification_time)) AS modified "
        f"FROM system.tables WHERE database = '{VERIFY_PREFIX}' FORMAT JSONEachRow"
    )
    for line in output.splitlines():
        modified = json.loads(line).get("modified") if line.strip() else None
        if modified:
            return now - int(modified) > _VERIFY_STALE_SECONDS
    return True


def drop_stale_scratch_databases(run: SqlRunner, now: float) -> list[str]:
    """Drop scratch databases an interrupted run left behind; return their names.

    A database goes when it is the legacy fixed-name copy or its embedded timestamp is
    more than an hour old, so a verify running in parallel is never touched.
    """
    output = run(
        f"SELECT name FROM system.databases WHERE name LIKE '{VERIFY_PREFIX}%' FORMAT JSONEachRow"
    )
    dropped = []
    for line in output.splitlines():
        if not line.strip():
            continue
        name = json.loads(line)["name"]
        match = _VERIFY_NAME.match(name)
        if (name == VERIFY_PREFIX and _legacy_scratch_is_idle(run, now)) or (
            match and now - int(match.group(1)) > _VERIFY_STALE_SECONDS
        ):
            run(f"DROP DATABASE IF EXISTS {name} SYNC")
            dropped.append(name)
    return dropped


def verify(
    run: SqlRunner,
    enabled: Sequence[str] | None = None,
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Build the whole store in a scratch database of its own, then drop it.

    Raises on the first statement ClickHouse rejects, so a broken projection, view,
    snapshot, or registry entry is caught before ``install`` drops the live loaders.
    Scratch databases left by interrupted runs are dropped first.
    """
    database = scratch_database_name(clock())
    statements = verify_statements(enabled, database)
    drop_stale_scratch_databases(run, clock())
    try:
        for statement in statements:
            run(statement)
    finally:
        run(f"DROP DATABASE IF EXISTS {database} SYNC")
    return {"database": database, "statements": len(statements), "passed": True}


def compare_statement(snapshot: Snapshot) -> str:
    """Return a query counting and hashing the rows of a snapshot and of its view.

    The hash is order-independent and covers every column.

    The newest 15 minutes are left out (the loaders keep adding rows there) and so is
    the oldest day of the 91-day horizon (rows at the edge lack their neighbours).
    """
    base = f"{DATABASE}.{snapshot.view}"
    column = snapshot.time_column
    window = (
        f"{column} >= now64(9) - INTERVAL {SNAPSHOT_DAYS} DAY"
        f" AND {column} < now64(9) - INTERVAL 15 MINUTE"
    )
    side = "count() AS rows, toString(sum(cityHash64(*))) AS hash"
    return (
        f"SELECT 'snapshot' AS side, {side} FROM {base}_snapshot WHERE {window}\n"
        f"UNION ALL\n"
        f"SELECT 'view' AS side, {side} FROM {base} WHERE {window}\n"
        f"SETTINGS max_threads = 2, {SNAPSHOT_FILTERS}\n"
        "FORMAT JSONEachRow"
    )


REFRESH_WAIT_BUDGET_SECONDS = 30 * 60
REFRESH_RETRY_PAUSE_SECONDS = 20


def refresh_view(
    run: SqlRunner,
    name: str,
    *,
    budget: float = REFRESH_WAIT_BUDGET_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Refresh one refreshable view now and wait for it; raise if the refresh failed.

    ``SYSTEM WAIT VIEW`` raises when the last attempt failed. A wait that outlives the
    runner timeout means the refresh is still running, so it waits again until
    ``budget`` seconds have passed since the refresh started.
    """
    target = f"{DATABASE}.{name}"
    deadline = clock() + budget
    try:
        run(f"SYSTEM REFRESH VIEW {target}")
        while True:
            try:
                run(f"SYSTEM WAIT VIEW {target}")
                return
            except StatementTimeoutError:
                if clock() >= deadline:
                    raise RefreshStillRunningError(
                        f"refresh of {name} failed: still running after {budget:.0f} s"
                    ) from None
    except RefreshStillRunningError:
        raise
    except FactsError as exc:
        raise FactsError(f"refresh of {name} failed: {exc}") from exc


def refresh_plan() -> list[tuple[str, str]]:
    """Return every install-time refresh in order as (refresher, upstream refresher or '').

    Minute loaders first, then every history, then every recent refresher, each after
    the snapshot it reads. A refresher whose upstream failed has nothing to build on.
    """
    plan = [(loader.name, "") for loader in LOADERS if loader.schedule == MINUTE_SCHEDULE]
    by_view = {snapshot.view: snapshot for snapshot in SNAPSHOTS}
    for part, snapshot in itertools.product((0, 1), SNAPSHOTS):
        upstream = by_view.get(snapshot.reads)
        plan.append((
            snapshot_refreshers(snapshot)[part],
            snapshot_refreshers(upstream)[part] if upstream else "",
        ))
    return plan


def refresh_known_names() -> list[str]:
    """Return the names of every loader and snapshot refresher that can be refreshed."""
    return [
        *(loader.name for loader in LOADERS),
        *(name for snapshot in SNAPSHOTS for name in snapshot_refreshers(snapshot)),
    ]


def refresh_with_retry(
    run: SqlRunner, name: str, sleep: Callable[[float], None] = time.sleep
) -> None:
    """Refresh one view; on failure pause and try once more (the server will not retry).

    A refresh still running after its budget is not retried: another request would
    queue a second full run behind the first.
    """
    try:
        refresh_view(run, name)
    except RefreshStillRunningError:
        raise
    except FactsError:
        sleep(REFRESH_RETRY_PAUSE_SECONDS)
        refresh_view(run, name)


def refresh_all(
    run: SqlRunner,
    plan: Sequence[tuple[str, str]],
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Refresh every planned view, then raise one error naming all that failed.

    A refresher whose upstream failed is skipped. One failure never leaves the later
    snapshots empty until their next slot.
    """
    failures: dict[str, str] = {}
    for name, upstream in plan:
        if upstream in failures:
            failures[name] = f"refresh of {name} skipped: {upstream} failed"
            continue
        try:
            refresh_with_retry(run, name, sleep)
        except FactsError as exc:
            failures[name] = str(exc)
    if failures:
        raise FactsError("; ".join(failures.values()))


def refresh_snapshots(run: SqlRunner, sleep: Callable[[float], None] = time.sleep) -> None:
    """Refresh every history, then every recent refresher, one at a time in SNAPSHOTS order."""
    refresh_all(run, [step for step in refresh_plan() if step[0].startswith("refresh_")], sleep)


def refresh_named(
    run: SqlRunner, name: str, sleep: Callable[[float], None] = time.sleep
) -> dict[str, Any]:
    """Refresh and wait for one named loader or snapshot refresher."""
    known = refresh_known_names()
    if name not in known:
        raise ValueError(f"unknown refresher {name}; known: {', '.join(known)}")
    refresh_with_retry(run, name, sleep)
    return {"refreshed": name}


def compare(run: SqlRunner) -> dict[str, Any]:
    """Rebuild every snapshot now, then check each matches a full read of its view.

    Proves the history/recent split loses and repeats nothing. It rereads every view
    in full, so it runs on demand (``facts verify --compare``), not on a schedule.
    """
    refresh_snapshots(run)
    snapshots: dict[str, dict[str, Any]] = {}
    for snapshot in SNAPSHOTS:
        output = run(compare_statement(snapshot))
        rows = [json.loads(line) for line in output.splitlines() if line]
        sides = {row["side"]: row for row in rows}
        held, full = sides["snapshot"], sides["view"]
        snapshots[snapshot.view] = {
            "snapshot_rows": int(held["rows"]),
            "view_rows": int(full["rows"]),
            "match": held["rows"] == full["rows"] and held["hash"] == full["hash"],
        }
    return {
        "snapshots": snapshots,
        "passed": all(result["match"] for result in snapshots.values()),
    }


def install(
    run: SqlRunner,
    enabled: Sequence[str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Create the facts database, loaders, views, snapshots, and registry tables.

    ``enabled`` holds the configured harness keys (None: every registry harness);
    unknown keys are rejected before any statement runs. The whole store is first
    built in a scratch database (``verify``), so SQL ClickHouse rejects never leaves
    the live store half installed.

    Every refreshable view is created ``EMPTY`` so creation fires none of them (an
    18-refresher burst once exhausted retries and left history empty). Install then
    refreshes the minute loaders, the histories, then the recents, one at a time. The
    server does not retry these out-of-schedule refreshes, so a failed one is retried
    once here after a short pause. Every refresher is attempted; a snapshot whose
    upstream failed is skipped, and one error names all failures at the end. Sweeps
    are not run now: the minute loaders cover the recent window and a sweep runs in
    its slot.
    """
    verify(run, enabled)
    statements = install_statements(enabled)
    for statement in statements:
        run(statement)
    refresh_all(run, refresh_plan(), sleep)
    registry = load_registry()
    harnesses = enabled_harnesses(registry, enabled)
    return {
        "database": DATABASE,
        "loaders": [loader.name for loader in LOADERS],
        "snapshots": [f"{snapshot.view}_snapshot" for snapshot in SNAPSHOTS],
        "registry": {"signals": len(registry.signals), "support": len(registry.support)},
        "harnesses": {
            "enabled": list(harnesses),
            "hidden_signals": list(
                dict.fromkeys(row["signal"] for row in hidden_signals(registry, harnesses))
            ),
        },
    }


def backfill(run: SqlRunner, *, days: int, now: datetime | None = None) -> dict[str, Any]:
    """Copy every retained source row from the last ``days`` days into the facts tables."""
    end = now or datetime.now(UTC)
    start = (end - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    counts = {}
    for target in _SELECT_FILES:
        for lower, upper in backfill_windows(start, end):
            try:
                run(backfill_statement(target, lower, upper))
            except FactsError as exc:
                raise FactsError(
                    f"backfill of {target} failed for window {lower:%Y-%m-%d %H:%M:%S}"
                    f" to {upper:%Y-%m-%d %H:%M:%S} UTC (rerun is idempotent): {exc}"
                ) from exc
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

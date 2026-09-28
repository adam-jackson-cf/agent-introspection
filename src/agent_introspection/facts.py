"""ClickHouse-materialized producer facts for the dashboard.

Refreshable materialized views copy curated producer spans and logs from the
SigNoz tables into the durable ``introspection`` database every minute. The
dashboard then reads small aggregates from those tables and their views.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from typing import Any

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


def install_statements() -> list[str]:
    """Return the ordered idempotent statements that create tables, loaders, and views.

    Loaders hold no state, so they are recreated to pick up projection changes.
    """
    return [
        _sql("001_tables.sql"),
        *(f"DROP VIEW IF EXISTS {DATABASE}.{loader.name}" for loader in LOADERS),
        *(loader_ddl(loader) for loader in LOADERS),
        _sql("003_views.sql"),
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
    """Create the facts database, loaders, and views if they do not exist."""
    for statement in install_statements():
        run(statement)
    return {"database": DATABASE, "loaders": [loader.name for loader in LOADERS]}


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

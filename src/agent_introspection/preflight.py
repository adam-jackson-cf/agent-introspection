"""Read-only checks that a SigNoz ClickHouse can host the facts store.

``facts preflight`` runs these before ``facts install`` against an existing or local
SigNoz. Nothing is created or written: grants are read with ``SHOW GRANTS``, source
tables through ``system.columns`` and ``WHERE 0`` probes.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any, Final
from urllib.parse import urlsplit

from agent_introspection.config import AppConfig
from agent_introspection.facts import DATABASE, FactsError, SqlRunner, sql_string

# Refreshable materialized views are production ready (no experimental setting) from
# ClickHouse 24.10; APPEND arrived in 24.9 and DEPENDS ON with the feature in 23.12.
# `facts install` sets no experimental flag, so 24.10 is the minimum.
MINIMUM_VERSION: Final = (24, 10)

PRODUCER_SERVICES: Final = (
    "codex-app-server",
    "codex_cli_rs",
    "codex_exec",
    "oh-my-pi",
    "claude-code",
)

# The SigNoz columns facts_sql/select_spans.sql and select_logs.sql read.
SPAN_TABLE: Final = ("signoz_traces", "distributed_signoz_index_v3")
SPAN_COLUMNS: Final = (
    "timestamp",
    "duration_nano",
    "serviceName",
    "name",
    "traceID",
    "spanID",
    "parentSpanID",
    "has_error",
    "status_code_string",
    "status_message",
    "attributes_string",
    "attributes_number",
    "attributes_bool",
)
LOG_TABLE: Final = ("signoz_logs", "distributed_logs_v2")
LOG_COLUMNS: Final = (
    "timestamp",
    "observed_timestamp",
    "id",
    "trace_id",
    "span_id",
    "severity_text",
    "attributes_string",
    "attributes_number",
    "attributes_bool",
    "resource",
)

# Privileges `facts install`, `backfill`, `sync`, and the loaders need on the facts
# database, each with the broader privileges that imply it.
REQUIRED_PRIVILEGES: Final = {
    "CREATE DATABASE": ("CREATE",),
    "CREATE TABLE": ("CREATE",),
    "CREATE VIEW": ("CREATE",),
    "DROP TABLE": ("DROP",),
    "DROP VIEW": ("DROP",),
    "INSERT": (),
    "SELECT": (),
}
_ALL = ("ALL", "ALL PRIVILEGES")
_FACTS_SCOPES = ("*.*", f"{DATABASE}.*")
_GRANT_LINE = re.compile(r"^(GRANT|REVOKE) (.+?) ON (\S+) (?:TO|FROM) ")

type Check = dict[str, Any]


def _check(name: str, status: str, detail: str, **extra: Any) -> Check:
    return {"name": name, "status": status, "detail": detail, **extra}


def _version(run: SqlRunner) -> Check:
    version = run("SELECT version()").strip()
    match = re.match(r"(\d+)\.(\d+)", version)
    minimum = ".".join(map(str, MINIMUM_VERSION))
    if match is None:
        return _check("clickhouse_version", "fail", f"unparseable version {version!r}")
    ok = (int(match[1]), int(match[2])) >= MINIMUM_VERSION
    return _check(
        "clickhouse_version",
        "pass" if ok else "fail",
        f"ClickHouse {version}; refreshable materialized views with APPEND and "
        f"DEPENDS ON need {minimum} or later",
        version=version,
        minimum=minimum,
    )


def _transport(config: AppConfig) -> Check:
    url = urlsplit(config.signoz.clickhouse_url or "")
    # The config only accepts local hosts, so this records what the CLI talks to.
    return _check("transport", "pass", f"{url.scheme} to {url.hostname} on this machine")


def _source(run: SqlRunner, table: tuple[str, str], columns: tuple[str, ...]) -> Check:
    database, name = table
    name_check = f"{database}.{name}"
    found = run(
        "SELECT name, type FROM system.columns WHERE database = "
        f"{sql_string(database)} AND table = {sql_string(name)} FORMAT JSONEachRow"
    )
    types = {row["name"]: row["type"] for row in map(json.loads, found.splitlines()) if row}
    missing = [column for column in columns if column not in types]
    if missing:
        return _check(name_check, "fail", "missing or not visible columns", missing=missing)
    if "resource" in columns and not types["resource"].startswith("JSON"):
        return _check(name_check, "fail", f"resource is {types['resource']}, expected JSON")
    probe = ", ".join(f"`{column}`" for column in columns)
    if "resource" in columns:
        probe += ", resource.`service.name`::String"
    run(f"SELECT {probe} FROM {database}.{name} WHERE 0 FORMAT Null")
    return _check(name_check, "pass", f"{len(columns)} columns present and readable")


def parse_grants(text: str) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """Return (privilege, scope) pairs granted and revoked by ``SHOW GRANTS`` output.

    Column-level grants such as ``SELECT(a, b)`` are kept with their parentheses, so
    they never count as a table-wide privilege.
    """
    granted: set[tuple[str, str]] = set()
    revoked: set[tuple[str, str]] = set()
    for line in text.splitlines():
        match = _GRANT_LINE.match(line.strip())
        if match is None:
            continue
        privileges = re.sub(r"\([^)]*\)", "()", match[2])
        target = granted if match[1] == "GRANT" else revoked
        target.update((privilege.strip(), match[3]) for privilege in privileges.split(","))
    return granted, revoked


def missing_privileges(text: str, *, database_exists: bool) -> list[str]:
    """Return the required facts-database privileges ``SHOW GRANTS`` does not give.

    A revoke on the facts database or any of its tables counts against the privilege,
    so partial revokes fail closed.
    """
    granted, revoked = parse_grants(text)
    missing = []
    for required in _required(database_exists):
        names = {required, *REQUIRED_PRIVILEGES[required], *_ALL}
        has = any((name, scope) in granted for name in names for scope in _FACTS_SCOPES)
        blocked = any(
            name in names and (scope == "*.*" or scope.startswith(f"{DATABASE}."))
            for name, scope in revoked
        )
        if not has or blocked:
            missing.append(required)
    return missing


def _required(database_exists: bool) -> list[str]:
    return [
        privilege
        for privilege in REQUIRED_PRIVILEGES
        if not (privilege == "CREATE DATABASE" and database_exists)
    ]


def _grant_gaps(run: SqlRunner, database_exists: bool) -> tuple[list[str], str]:
    """Return missing privileges and how they were determined.

    ``CHECK GRANT`` (recent ClickHouse releases) resolves roles and partial revokes on the
    server; older servers fall back to parsing ``SHOW GRANTS``.
    """
    try:
        missing = [
            privilege
            for privilege in _required(database_exists)
            if run(f"CHECK GRANT {privilege} ON {DATABASE}.*").strip() != "1"
        ]
        return missing, "CHECK GRANT"
    except FactsError:
        pass
    try:
        return missing_privileges(
            run("SHOW GRANTS FINAL"), database_exists=database_exists
        ), "SHOW GRANTS FINAL"
    except FactsError:
        return missing_privileges(
            run("SHOW GRANTS"), database_exists=database_exists
        ), "SHOW GRANTS without role expansion"


def _grants(run: SqlRunner) -> Check:
    exists = run(f"SELECT count() FROM system.databases WHERE name = {sql_string(DATABASE)}")
    database_exists = exists.strip() == "1"
    missing, method = _grant_gaps(run, database_exists)
    detail = (
        f"missing on {DATABASE}.*: {', '.join(missing)}"
        if missing
        else f"database {DATABASE} {'exists' if database_exists else 'can be created'}"
    )
    return _check(
        "facts_database_grants",
        "fail" if missing else "pass",
        f"{detail} (via {method})",
        database_exists=database_exists,
        missing=missing,
    )


def _view_refreshes(run: SqlRunner) -> Check:
    run("SELECT view, status FROM system.view_refreshes WHERE 0 FORMAT Null")
    return _check("view_refreshes_readable", "pass", "facts status can read loader state")


def _producer_data(run: SqlRunner) -> Check:
    services = ", ".join(map(sql_string, PRODUCER_SERVICES))
    rows = run(
        "SELECT source, service, count() AS rows FROM ("
        "SELECT 'spans' AS source, serviceName AS service "
        f"FROM {'.'.join(SPAN_TABLE)} WHERE serviceName IN ({services}) "
        "AND timestamp > now() - INTERVAL 7 DAY "
        "UNION ALL SELECT 'logs' AS source, resource.`service.name`::String AS service "
        f"FROM {'.'.join(LOG_TABLE)} WHERE resource.`service.name`::String IN ({services}) "
        "AND timestamp > toUInt64(toUnixTimestamp(now() - INTERVAL 7 DAY)) * 1000000000"
        ") GROUP BY source, service ORDER BY source, service FORMAT JSONEachRow"
    )
    counts = [
        {**row, "rows": int(row["rows"])} for row in map(json.loads, rows.splitlines()) if row
    ]
    detail = (
        f"{len({row['service'] for row in counts})} producer services sent data in 7 days"
        if counts
        else "no producer data in 7 days; configure producers' OTLP export before install"
    )
    return _check("producer_data", "info", detail, counts=counts)


def _otlp(config: AppConfig) -> Check:
    endpoint = config.signoz.otlp_endpoint
    detail = (
        f"producers should export to {endpoint}"
        if endpoint is not None
        else "no signoz.otlp_endpoint; producers use the local default localhost:4317/4318"
    )
    return _check("otlp_endpoint", "info", detail, endpoint=endpoint)


def _guarded(name: str, check: Callable[[], Check], *, status: str = "fail") -> Check:
    try:
        return check()
    except FactsError as exc:
        return _check(name, status, str(exc))


def preflight(run: SqlRunner, config: AppConfig) -> dict[str, Any]:
    """Run every read-only check; ``passed`` is false when any check fails."""
    checks = [_transport(config)] if config.signoz.mode == "http" else []
    version = _guarded("clickhouse_version", lambda: _version(run))
    checks.append(version)
    if version["status"] == "pass" or "version" in version:
        checks += [
            _guarded(".".join(SPAN_TABLE), lambda: _source(run, SPAN_TABLE, SPAN_COLUMNS)),
            _guarded(".".join(LOG_TABLE), lambda: _source(run, LOG_TABLE, LOG_COLUMNS)),
            _guarded("facts_database_grants", lambda: _grants(run)),
            _guarded("view_refreshes_readable", lambda: _view_refreshes(run)),
            _guarded("producer_data", lambda: _producer_data(run), status="info"),
        ]
    checks.append(_otlp(config))
    return {
        "mode": config.signoz.mode,
        "passed": all(check["status"] != "fail" for check in checks),
        "checks": checks,
    }

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agent_introspection import facts, preflight
from agent_introspection.config import AppConfig, ConfigurationError, load_config, parse_config
from agent_introspection.facts import FactsError

ALL_GRANTS = "GRANT SELECT, INSERT, ALTER, CREATE, DROP ON *.* TO default\n"


def _columns(names: tuple[str, ...], resource: str = "JSON(max_dynamic_paths=100)") -> str:
    return "\n".join(
        json.dumps({"name": name, "type": resource if name == "resource" else "String"})
        for name in names
    )


def fake_clickhouse(
    *,
    version: str = "25.5.6.14",
    span_columns: tuple[str, ...] = preflight.SPAN_COLUMNS,
    grants: dict[str, str] | None = None,
    show_grants: str = ALL_GRANTS,
    database_exists: bool = False,
) -> tuple[list[str], facts.SqlRunner]:
    """Answer each preflight statement; CHECK GRANT is unsupported unless given."""
    executed: list[str] = []

    def run(sql: str) -> str:
        executed.append(sql)
        answers = {
            "SELECT version()": version,
            "system.databases": "1" if database_exists else "0",
            "table = 'distributed_signoz_index_v3'": _columns(span_columns),
            "table = 'distributed_logs_v2'": _columns(preflight.LOG_COLUMNS),
            "SHOW GRANTS": show_grants,
            "GROUP BY source, service": json.dumps({
                "source": "spans",
                "service": "claude-code",
                "rows": "3",
            }),
        }
        if sql.startswith("CHECK GRANT"):
            if grants is None:
                raise FactsError("Code: 62. DB::Exception: Syntax error")
            return grants.get(sql.split(" ON ")[0].removeprefix("CHECK GRANT "), "1")
        return next((answer for key, answer in answers.items() if key in sql), "")

    return executed, run


def _statuses(result: dict[str, Any]) -> dict[str, str]:
    return {check["name"]: check["status"] for check in result["checks"]}


def test_preflight_passes_a_compatible_server_and_only_reads() -> None:
    executed, run = fake_clickhouse(grants={})

    result = preflight.preflight(run, AppConfig())

    assert result["passed"] is True
    assert _statuses(result) == {
        "transport": "pass",
        "clickhouse_version": "pass",
        "signoz_traces.distributed_signoz_index_v3": "pass",
        "signoz_logs.distributed_logs_v2": "pass",
        "facts_database_grants": "pass",
        "view_refreshes_readable": "pass",
        "producer_data": "info",
        "otlp_endpoint": "info",
    }
    writes = ("CREATE ", "DROP ", "INSERT ", "ALTER ", "TRUNCATE ")
    assert not [sql for sql in executed if sql.startswith(writes)]


def test_preflight_fails_old_servers_missing_columns_and_privileges() -> None:
    _, run = fake_clickhouse(
        version="24.8.4.13",
        span_columns=tuple(c for c in preflight.SPAN_COLUMNS if c != "status_code_string"),
        grants={"CREATE VIEW": "0"},
    )

    result = preflight.preflight(run, AppConfig())
    checks = {check["name"]: check for check in result["checks"]}

    assert result["passed"] is False
    assert checks["clickhouse_version"]["status"] == "fail"
    assert checks["signoz_traces.distributed_signoz_index_v3"]["missing"] == ["status_code_string"]
    assert checks["facts_database_grants"]["missing"] == ["CREATE VIEW"]


def test_grant_parsing_handles_hierarchy_scopes_columns_and_revokes() -> None:
    scoped = (
        "GRANT CREATE TABLE, CREATE VIEW, DROP TABLE, DROP VIEW, INSERT ON introspection.* TO u\n"
        "GRANT SELECT(a, b) ON introspection.* TO u\n"
        "GRANT SELECT ON signoz_traces.* TO u\n"
        "GRANT reader TO u\n"
    )
    assert preflight.missing_privileges(scoped, database_exists=True) == ["SELECT"]
    assert preflight.missing_privileges(scoped, database_exists=False) == [
        "CREATE DATABASE",
        "SELECT",
    ]
    assert preflight.missing_privileges("GRANT ALL ON *.* TO u", database_exists=False) == []
    revoked = "GRANT ALL ON *.* TO u\nREVOKE DROP ON introspection.spans FROM u"
    assert preflight.missing_privileges(revoked, database_exists=True) == [
        "DROP TABLE",
        "DROP VIEW",
    ]


def test_preflight_falls_back_to_show_grants_without_check_grant() -> None:
    _, run = fake_clickhouse(show_grants="GRANT SELECT ON *.* TO u", database_exists=True)

    check = {c["name"]: c for c in preflight.preflight(run, AppConfig())["checks"]}[
        "facts_database_grants"
    ]

    assert check["status"] == "fail"
    assert "SHOW GRANTS FINAL" in check["detail"]
    assert "INSERT" in check["missing"]


def test_only_a_local_signoz_is_accepted() -> None:
    _, run = fake_clickhouse(grants={})
    for host in ("ch.example.com", "10.0.0.5", "signoz.local"):
        with pytest.raises(ConfigurationError, match="this machine"):
            parse_config({"signoz": {"clickhouse_url": f"http://{host}:8123"}})
    with pytest.raises(ConfigurationError, match="this machine"):
        parse_config({
            "signoz": {
                "clickhouse_url": "http://127.0.0.1:8123",
                "otlp_endpoint": "http://otel.io",
            }
        })
    for host in ("127.0.0.1", "[::1]", "localhost", "signoz-clickhouse.orb.local"):
        local = parse_config({"signoz": {"clickhouse_url": f"http://{host}:8123"}})
        assert _statuses(preflight.preflight(run, local))["transport"] == "pass"


def test_unreachable_server_fails_without_running_later_checks() -> None:
    def down(_sql: str) -> str:
        raise FactsError("ClickHouse HTTP request failed: connection refused")

    result = preflight.preflight(down, AppConfig())

    assert result["passed"] is False
    assert _statuses(result) == {
        "transport": "pass",
        "clickhouse_version": "fail",
        "otlp_endpoint": "info",
    }


_A_WINDOW = (datetime(2026, 9, 27, tzinfo=UTC), datetime(2026, 9, 27, 6, tzinfo=UTC))


def _services_read(select: str, column: str) -> list[str]:
    listed = select.split(f"WHERE {column} IN (", 1)[1].split(")", 1)[0]
    return [item.strip().strip("'") for item in listed.split(",")]


_BROKEN_REGISTRY_IMPORT = """
from agent_introspection import facts
original = facts._sql
facts._sql = lambda name: "[[broken\\n" if name == facts.REGISTRY_FILE else original(name)
import agent_introspection.cli
"""


def test_a_registry_that_does_not_parse_never_stops_the_cli_from_starting() -> None:
    # `hook`, `spool sync`, and `doctor` must start while the registry is mid-edit;
    # only the commands that read it fail.
    result = subprocess.run(
        [sys.executable, "-c", _BROKEN_REGISTRY_IMPORT],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_checked_columns_and_services_are_the_ones_the_projections_read() -> None:
    spans = facts.render_select("select_spans.sql", "1")
    logs = facts.render_select("select_logs.sql", "1")
    hook_events = facts.render_select("select_hook_events.sql", "1")
    registry = list(facts.load_registry().harnesses)

    for column in preflight.SPAN_COLUMNS:
        assert column in spans
    for column in preflight.LOG_COLUMNS:
        assert column in logs + hook_events
    assert "resources_string" in hook_events
    checked: list[str] = []

    def run(sql: str) -> str:
        checked.append(sql)
        return ""

    preflight._producer_data(run)
    assert _services_read(checked[0], "serviceName") == registry
    assert _services_read(checked[0], "resource.`service.name`::String") == registry
    assert _services_read(spans, "serviceName") == registry
    assert _services_read(logs, "resource.`service.name`::String") == registry
    rendered = [facts.loader_ddl(loader) for loader in facts.LOADERS]
    rendered += [facts.backfill_statement(target, *_A_WINDOW) for target in ("spans", "logs")]
    assert not [sql for sql in rendered if "{services}" in sql]


def _local_command(monkeypatch: pytest.MonkeyPatch, returncode: int, stdout: str) -> None:
    """Answer the Docker context lookup or password command without running anything."""

    def run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, returncode, stdout, "")

    monkeypatch.setattr(facts, "_run_local_command", run)


@dataclass(frozen=True)
class SetupCase:
    """A config whose connection cannot be set up, and what the local command answers."""

    config: str
    reason: str
    statuses: dict[str, str]
    returncode: int = 0
    stdout: str = ""


_HTTP = '[signoz]\nclickhouse_url = "http://127.0.0.1:8123"\n'
_HTTP_STATUSES = {"transport": "pass", "connection": "fail", "otlp_endpoint": "info"}
_SECRET = "sentinel-not-a-password"


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            SetupCase(
                config=_HTTP + 'clickhouse_password_env = "AI_PREFLIGHT_TEST_PASSWORD"\n',
                reason="environment variable AI_PREFLIGHT_TEST_PASSWORD",
                statuses=_HTTP_STATUSES,
            ),
            id="password-variable-unset",
        ),
        pytest.param(
            SetupCase(
                config=_HTTP + 'clickhouse_password_command = ["security", "find"]\n',
                reason="printed no password (exit 44)",
                statuses=_HTTP_STATUSES,
                returncode=44,
                stdout=_SECRET,
            ),
            id="password-command-fails",
        ),
    ],
)
def test_preflight_reports_setup_errors_as_a_failing_connection_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: SetupCase,
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(case.config)
    monkeypatch.delenv("AI_PREFLIGHT_TEST_PASSWORD", raising=False)
    _local_command(monkeypatch, case.returncode, case.stdout)

    result = preflight.report(lambda: load_config(config), facts.runner)
    connection = {check["name"]: check for check in result["checks"]}["connection"]

    assert result["passed"] is False
    assert _statuses(result) == case.statuses
    assert connection["detail"].startswith("ConfigurationError: ")
    assert case.reason in connection["detail"]
    assert _SECRET not in json.dumps(result)


def test_preflight_reports_an_unloadable_config_as_a_failing_check(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text("[signoz\n")

    result = preflight.report(lambda: load_config(config), facts.runner)

    assert result["passed"] is False
    assert [check["name"] for check in result["checks"]] == ["configuration"]
    assert result["checks"][0]["detail"].startswith("ConfigurationError: invalid TOML")


def test_a_runner_that_cannot_be_built_still_reports_every_local_check() -> None:
    def refuse(_config: AppConfig) -> facts.SqlRunner:
        raise ConfigurationError("signoz.clickhouse_url is not configured")

    result = preflight.report(AppConfig, refuse)

    assert result["passed"] is False
    assert _statuses(result) == {"transport": "pass", "connection": "fail", "otlp_endpoint": "info"}

    _, healthy = fake_clickhouse(grants={})
    assert preflight.report(AppConfig, lambda _config: healthy)["passed"] is True


def test_an_additional_machine_fails_the_connection_and_names_the_hosts_collector(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        '[machine]\nrole = "additional"\nexport_endpoint = "http://signoz-host.example:4318"\n'
    )

    result = preflight.report(lambda: load_config(config), facts.runner)

    checks = {check["name"]: check for check in result["checks"]}
    assert checks["connection"]["status"] == "fail"
    assert (
        'this additional machine ([machine] role = "additional")' in checks["connection"]["detail"]
    )
    assert checks["otlp_endpoint"]["endpoint"] == "http://signoz-host.example:4318"

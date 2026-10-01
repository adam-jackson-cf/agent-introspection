from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_introspection import cli, facts, preflight
from agent_introspection.cli import EXIT_FACTS, main
from agent_introspection.config import AppConfig, ConfigurationError, parse_config
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
            "GROUP BY source, service": json.dumps(
                {"source": "spans", "service": "claude-code", "rows": "3"}
            ),
        }
        if sql.startswith("CHECK GRANT"):
            if grants is None:
                raise FactsError("Code: 62. DB::Exception: Syntax error")
            return grants.get(sql.split(" ON ")[0].removeprefix("CHECK GRANT "), "1")
        return next((answer for key, answer in answers.items() if key in sql), "")

    return executed, run


def _statuses(result: dict[str, object]) -> dict[str, str]:
    return {check["name"]: check["status"] for check in result["checks"]}  # type: ignore[attr-defined]


def test_preflight_passes_a_compatible_server_and_only_reads() -> None:
    executed, run = fake_clickhouse(grants={})

    result = preflight.preflight(run, AppConfig())

    assert result["passed"] is True
    assert result["mode"] == "docker"
    assert _statuses(result) == {
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
        parse_config(
            {
                "signoz": {
                    "clickhouse_url": "http://127.0.0.1:8123",
                    "otlp_endpoint": "http://otel.io",
                }
            }
        )
    for host in ("127.0.0.1", "[::1]", "localhost", "signoz-clickhouse.orb.local"):
        local = parse_config({"signoz": {"clickhouse_url": f"http://{host}:8123"}})
        assert _statuses(preflight.preflight(run, local))["transport"] == "pass"


def test_unreachable_server_fails_without_running_later_checks() -> None:
    def down(_sql: str) -> str:
        raise FactsError("ClickHouse HTTP request failed: connection refused")

    result = preflight.preflight(down, AppConfig())

    assert result["passed"] is False
    assert _statuses(result) == {"clickhouse_version": "fail", "otlp_endpoint": "info"}


def test_checked_columns_and_services_are_the_ones_the_projections_read() -> None:
    spans = facts._sql("select_spans.sql")
    logs = facts._sql("select_logs.sql")

    for column in preflight.SPAN_COLUMNS:
        assert column in spans
    for column in preflight.LOG_COLUMNS:
        assert column in logs
    for service in preflight.PRODUCER_SERVICES:
        assert f"'{service}'" in spans
        assert f"'{service}'" in logs


def test_cli_preflight_prints_every_check_and_exits_non_zero_on_failure(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "config.toml"
    config.write_text("")
    _, run = fake_clickhouse(version="23.8.1", grants={})
    monkeypatch.setattr(cli.facts, "runner", lambda _config: run)

    assert main(["--config", str(config), "facts", "preflight"]) == EXIT_FACTS
    assert json.loads(capsys.readouterr().out)["passed"] is False

    _, healthy = fake_clickhouse(grants={})
    monkeypatch.setattr(cli.facts, "runner", lambda _config: healthy)
    assert main(["--config", str(config), "facts", "preflight"]) == 0

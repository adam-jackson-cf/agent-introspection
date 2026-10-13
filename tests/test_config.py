from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from agent_introspection.config import (
    ConfigurationError,
    MachineRole,
    load_config,
    parse_config,
    require_host,
    resolve_otlp_logs_endpoint,
)


def test_documented_defaults_are_canonical() -> None:
    config = parse_config({})

    assert config.database.path == (
        Path.home() / ".local/share/agent-introspection/introspection.sqlite3"
    )
    assert config.database.busy_timeout_ms == 5_000
    assert config.signoz.clickhouse_container == "signoz-clickhouse"
    assert config.signoz.docker_context is None


def test_parse_config_expands_paths_and_preserves_explicit_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INTROSPECTION_ROOT", str(tmp_path))

    config = parse_config({
        "database": {"path": "$INTROSPECTION_ROOT/state.sqlite3", "busy_timeout_ms": 12_000},
        "signoz": {"clickhouse_container": "clickhouse", "docker_context": "desktop"},
    })

    assert config.database.path == tmp_path / "state.sqlite3"
    assert config.database.busy_timeout_ms == 12_000
    assert config.signoz.clickhouse_container == "clickhouse"
    assert config.signoz.docker_context == "desktop"


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ({"scheduler": {"interval_seconds": 300}}, "unsupported keys in configuration: scheduler"),
        ({"signoz": {"otlp_http_endpoint": "x"}}, "unsupported keys in signoz"),
        ({"database": {"busy_timeout_ms": 0}}, "positive integer"),
        ({"database": {"path": ""}}, "non-empty string"),
        ({"signoz": "orbstack"}, "TOML table"),
    ],
)
def test_parse_config_rejects_unknown_or_invalid_values(
    document: dict[str, object], message: str
) -> None:
    with pytest.raises(ConfigurationError, match=message):
        parse_config(document)


def test_load_config_uses_defaults_only_for_the_absent_canonical_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.toml"
    with pytest.raises(ConfigurationError, match="does not exist"):
        load_config(missing)
    invalid = tmp_path / "invalid.toml"
    invalid.write_text("[database\n")
    with pytest.raises(ConfigurationError, match="invalid TOML"):
        load_config(invalid)


@pytest.mark.parametrize(
    ("document", "retired"),
    [
        ({"scheduler": {"interval_seconds": 300}}, "scheduler"),
        ({"signoz": {"otlp_http_endpoint": "x"}}, "otlp_http_endpoint"),
    ],
)
def test_retired_keys_are_named_as_safe_to_delete(
    document: dict[str, object], retired: str
) -> None:
    with pytest.raises(ConfigurationError, match=f"{retired} retired with the scan pipeline"):
        parse_config(document)


EXAMPLE = Path(__file__).parents[1] / "config.example.toml"
# A commented-out table header or `key = value` line in the example.
COMMENTED_SETTING = re.compile(r"^# (\[[\w.]+\]|\w+ = .*)$")
SIGNOZ_MODE_KEYS = {
    "docker": {"clickhouse_container", "docker_context"},
    "http": {"clickhouse_url", "clickhouse_user", "clickhouse_password_command"},
}


def _example_with_every_setting(mode: str) -> str:
    """Return the example with each commented setting switched on, for one [signoz] mode."""
    other = SIGNOZ_MODE_KEYS["http" if mode == "docker" else "docker"]
    lines: list[str] = []
    table = ""
    for line in EXAMPLE.read_text().splitlines():
        match = COMMENTED_SETTING.match(line)
        text = match.group(1) if match else line
        if text.startswith("["):
            table = text.strip("[]")
        if not (table == "signoz" and text.split(" = ", 1)[0] in other):
            lines.append(text)
    return "\n".join(lines)


def test_example_configuration_is_valid() -> None:
    assert load_config(EXAMPLE).signoz.clickhouse_container == "signoz-clickhouse"


@pytest.mark.parametrize("mode", ["docker", "http"])
def test_every_setting_the_example_documents_is_accepted(tmp_path: Path, mode: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(_example_with_every_setting(mode))

    config = load_config(path)

    assert config.signoz.mode == mode
    assert config.dashboard_clickhouse_url is not None
    assert config.harnesses_enabled is not None
    assert config.hooks_otlp_logs_endpoint is not None
    assert config.session_roots["codex"]
    assert config.labels_openrouter_key_command is not None
    assert (config.signoz.clickhouse_password_command is not None) == (mode == "http")
    assert config.machine.role is MachineRole.ADDITIONAL
    assert config.collector_endpoint == config.machine.export_endpoint


def test_the_openrouter_key_command_is_an_argv_list_and_never_the_key() -> None:
    assert parse_config({}).labels_openrouter_key_command is None
    config = parse_config({"labels": {"openrouter_key_command": ["security", "-w"]}})
    assert config.labels_openrouter_key_command == ("security", "-w")
    for bad in ("sk-or-v1-0000", [], ["security", ""], [1]):
        with pytest.raises(ConfigurationError, match="openrouter_key_command must be a non-empty"):
            parse_config({"labels": {"openrouter_key_command": bad}})
    with pytest.raises(ConfigurationError, match="unsupported keys in labels: openrouter_api_key"):
        parse_config({"labels": {"openrouter_api_key": "sk-or-v1-0000"}})
    with pytest.raises(ConfigurationError, match="labels must be a TOML table"):
        parse_config({"labels": "security"})


def test_http_mode_reads_url_user_and_password_variable_name() -> None:
    config = parse_config({
        "signoz": {
            "clickhouse_url": "http://signoz-clickhouse.orb.local:8123/",
            "clickhouse_user": "introspection",
            "clickhouse_password_env": "INTROSPECTION_CLICKHOUSE_PASSWORD",
            "otlp_endpoint": "http://localhost:4318",
        }
    })

    assert config.signoz.mode == "http"
    assert config.signoz.clickhouse_url == "http://signoz-clickhouse.orb.local:8123"
    assert config.signoz.clickhouse_user == "introspection"
    assert config.signoz.clickhouse_password_env == "INTROSPECTION_CLICKHOUSE_PASSWORD"
    assert config.signoz.otlp_endpoint == "http://localhost:4318"
    assert parse_config({}).signoz.mode == "docker"


@pytest.mark.parametrize(
    ("signoz", "message"),
    [
        (
            {"clickhouse_url": "http://localhost:8123", "clickhouse_container": "c"},
            "exactly one connection mode",
        ),
        ({"clickhouse_user": "u"}, "need clickhouse_url"),
        ({"clickhouse_url": "ftp://ch"}, "http:// or https://"),
        ({"clickhouse_url": "http://user:secret@localhost:8123"}, "must not embed credentials"),
        (
            {"clickhouse_url": "http://localhost:8123", "clickhouse_password_env": "hunter 2"},
            "name an environment variable",
        ),
        (
            {"clickhouse_url": "http://localhost:8123", "clickhouse_database": "x"},
            "unsupported keys",
        ),
    ],
)
def test_http_mode_rejects_mixed_modes_and_inline_secrets(
    signoz: dict[str, object], message: str
) -> None:
    with pytest.raises(ConfigurationError, match=message):
        parse_config({"signoz": signoz})


def test_password_command_is_an_argv_list_and_excludes_the_env_variable() -> None:
    url = "http://127.0.0.1:8123"
    config = parse_config({
        "signoz": {"clickhouse_url": url, "clickhouse_password_command": ["security", "-w"]}
    })
    assert config.signoz.clickhouse_password_command == ("security", "-w")
    for bad in ("security -w", [], ["security", ""]):
        with pytest.raises(ConfigurationError, match="list of arguments"):
            parse_config({"signoz": {"clickhouse_url": url, "clickhouse_password_command": bad}})
    with pytest.raises(ConfigurationError, match="not both"):
        parse_config({
            "signoz": {
                "clickhouse_url": url,
                "clickhouse_password_env": "CH_PASSWORD",
                "clickhouse_password_command": ["security"],
            }
        })


def test_dashboard_url_is_optional_and_local() -> None:
    config = parse_config({"dashboard": {"clickhouse_url": "http://127.0.0.1:8123/"}})
    assert config.dashboard_clickhouse_url == "http://127.0.0.1:8123"
    assert parse_config({}).dashboard_clickhouse_url is None
    with pytest.raises(ConfigurationError, match="this machine"):
        parse_config({"dashboard": {"clickhouse_url": "http://ch.example.com:8123"}})


def test_enabled_harnesses_are_optional_and_kept_as_listed() -> None:
    assert parse_config({}).harnesses_enabled is None
    config = parse_config({"harnesses": {"enabled": ["claude-code", "codex"]}})

    assert config.harnesses_enabled == ("claude-code", "codex")


@pytest.mark.parametrize(
    ("harnesses", "message"),
    [
        ({"enabled": []}, "non-empty list"),
        ({"enabled": "claude-code"}, "non-empty list"),
        ({"enabled": ["claude-code", ""]}, "non-empty list"),
        ({"enabled": ["codex", "codex"]}, "more than once"),
        ({"disabled": ["codex"]}, "unsupported keys in harnesses"),
    ],
)
def test_enabled_harnesses_reject_malformed_lists(
    harnesses: dict[str, object], message: str
) -> None:
    with pytest.raises(ConfigurationError, match=message):
        parse_config({"harnesses": harnesses})


def test_the_hooks_endpoint_is_optional_and_may_be_a_remote_host() -> None:
    assert parse_config({}).hooks_otlp_logs_endpoint is None
    url = "http://100.80.132.50:4318/v1/logs"
    assert parse_config({"hooks": {"otlp_logs_endpoint": url}}).hooks_otlp_logs_endpoint == url
    secure = "https://collector.example.net/v1/logs"
    config = parse_config({"hooks": {"otlp_logs_endpoint": secure}})
    assert config.hooks_otlp_logs_endpoint == secure


@pytest.mark.parametrize(
    ("hooks", "message"),
    [
        ({"otlp_logs_endpoint": "grpc://host:4317"}, "http:// or https://"),
        ({"otlp_logs_endpoint": "http://user:pw@host/v1/logs"}, "must not embed credentials"),
        ({"otlp_logs_endpoint": "http://user@host/v1/logs"}, "must not embed credentials"),
        ({"otlp_logs_endpoint": ""}, "non-empty string"),
        ({"endpoint": "http://host"}, "unsupported keys in hooks"),
    ],
)
def test_the_hooks_endpoint_rejects_bad_urls_and_unknown_keys(
    hooks: dict[str, object], message: str
) -> None:
    with pytest.raises(ConfigurationError, match=message):
        parse_config({"hooks": hooks})


def test_a_machine_is_the_signoz_host_unless_it_says_it_is_additional() -> None:
    host = parse_config({"signoz": {"otlp_endpoint": "http://127.0.0.1:4318"}})
    assert host.machine.role is MachineRole.HOST
    assert host.collector_endpoint == "http://127.0.0.1:4318"
    assert parse_config({"machine": {"role": "host"}}).collector_endpoint is None
    require_host(host, "facts")

    additional = parse_config({
        "machine": {"role": "additional", "export_endpoint": "https://signoz-host.example:4318/"},
        "signoz": {"otlp_endpoint": "http://127.0.0.1:4318"},
    })

    assert additional.machine.additional
    # The host's collector, even when the host's own [signoz] setting was copied over.
    assert additional.collector_endpoint == "https://signoz-host.example:4318"
    with pytest.raises(ConfigurationError, match=r'additional machine \(\[machine\] role = "addi'):
        require_host(additional, "this command needs SigNoz's ClickHouse")


@pytest.mark.parametrize(
    ("machine", "message"),
    [
        ({"role": "satellite"}, 'machine.role must be "host" or "additional"'),
        ({"role": "host", "export_endpoint": "http://h:4318"}, 'applies only to role = "addi'),
        ({"export_endpoint": "http://h:4318"}, 'applies only to role = "additional"'),
        ({"role": "additional"}, "export_endpoint is required"),
        ({"role": "additional", "export_endpoint": "grpc://h:4317"}, "http:// or https://"),
        (
            {"role": "additional", "export_endpoint": "http://user:pw@h:4318"},
            "must not embed credentials",
        ),
        ({"role": "additional", "export_endpoint": ""}, "non-empty string"),
        ({"role": "additional", "export_endpoint": "http://h:99999"}, "must name a valid port"),
        ({"role": "additional", "host": "h"}, "unsupported keys in machine: host"),
    ],
)
def test_the_machine_table_rejects_a_bad_role_or_export_endpoint(
    machine: dict[str, object], message: str
) -> None:
    with pytest.raises(ConfigurationError, match=re.escape(message)):
        parse_config({"machine": machine})


@pytest.mark.parametrize(
    "document",
    [
        {"signoz": {"otlp_endpoint": "http://127.0.0.1:4317"}},
        {"machine": {"role": "additional", "export_endpoint": "http://signoz-host.example:4317/"}},
    ],
)
def test_a_collector_endpoint_on_the_otlp_grpc_port_is_refused(
    document: dict[str, Any],
) -> None:
    # Every harness profile writes the collector endpoint with an OTLP/HTTP exporter.
    with pytest.raises(
        ConfigurationError, match=re.escape("OTLP/HTTP base URL (port 4318), not its OTLP/gRPC")
    ):
        parse_config(document)


PROTOCOL = "OTEL_EXPORTER_OTLP_PROTOCOL"
HTTP_ENV = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318/"}
DERIVED = "http://collector:4318/v1/logs"


@pytest.mark.parametrize(
    ("configured", "environ", "expected"),
    [
        ("http://cfg/v1/logs", HTTP_ENV, "http://cfg/v1/logs"),
        ("http://cfg/v1/logs", {PROTOCOL: "grpc", **HTTP_ENV}, "http://cfg/v1/logs"),
        (None, {}, None),
        (None, HTTP_ENV, DERIVED),
        (None, {PROTOCOL: "http/protobuf", **HTTP_ENV}, DERIVED),
        (None, {PROTOCOL: "http/json", **HTTP_ENV}, DERIVED),
        (None, {PROTOCOL: "grpc", **HTTP_ENV}, None),
        (None, {"OTEL_EXPORTER_OTLP_LOGS_PROTOCOL": "grpc", **HTTP_ENV}, None),
        (
            None,
            {"OTEL_EXPORTER_OTLP_LOGS_PROTOCOL": "http/json", PROTOCOL: "grpc", **HTTP_ENV},
            DERIVED,
        ),
        (
            None,
            {"OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": "http://logs:4318/custom"} | HTTP_ENV,
            "http://logs:4318/custom",
        ),
        (None, {"OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": "http://u:p@logs/x"}, None),
        (None, {"OTEL_EXPORTER_OTLP_ENDPOINT": "collector:4317"}, None),
    ],
)
def test_the_endpoint_comes_from_config_first_then_only_an_http_environment(
    configured: str | None, environ: dict[str, str], expected: str | None
) -> None:
    assert resolve_otlp_logs_endpoint(configured, environ) == expected

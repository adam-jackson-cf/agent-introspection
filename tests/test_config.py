from __future__ import annotations

from pathlib import Path

import pytest

from agent_introspection.config import ConfigurationError, load_config, parse_config


def test_documented_defaults_are_canonical() -> None:
    config = parse_config({})

    assert config.database.path == (
        Path.home() / ".local/share/agent-introspection/introspection.sqlite3"
    )
    assert config.database.busy_timeout_ms == 5_000
    assert config.signoz.clickhouse_container == "signoz-clickhouse"
    assert config.signoz.docker_context == "orbstack"


def test_parse_config_expands_paths_and_preserves_explicit_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INTROSPECTION_ROOT", str(tmp_path))

    config = parse_config(
        {
            "database": {"path": "$INTROSPECTION_ROOT/state.sqlite3", "busy_timeout_ms": 12_000},
            "signoz": {"clickhouse_container": "clickhouse", "docker_context": "desktop"},
        }
    )

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


def test_example_configuration_is_valid() -> None:
    example = Path(__file__).parents[1] / "config.example.toml"

    assert load_config(example).signoz.clickhouse_container == "signoz-clickhouse"

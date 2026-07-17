from __future__ import annotations

from pathlib import Path

import pytest

import agent_introspection.config as config_module
from agent_introspection.config import (
    ConfigurationError,
    load_config,
    parse_config,
)


def test_documented_defaults_are_canonical() -> None:
    config = parse_config({})

    assert config.database.path == (
        Path.home() / ".local/share/agent-introspection/introspection.sqlite3"
    )
    assert config.database.busy_timeout_ms == 5_000
    assert config.signoz.docker_context == "orbstack"
    assert config.signoz.docker_host == "unix:///Users/adamjackson/.orbstack/run/docker.sock"
    assert config.scheduler.interval_seconds == 3_600


def test_parse_config_expands_paths_and_preserves_explicit_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INTROSPECTION_ROOT", str(tmp_path))

    config = parse_config(
        {
            "database": {
                "path": "$INTROSPECTION_ROOT/state.sqlite3",
                "busy_timeout_ms": 12_000,
            },
            "signoz": {"compose_directory": "$INTROSPECTION_ROOT/signoz"},
            "scheduler": {
                "timezone": "Europe/London",
                "interval_seconds": 1_800,
                "lease_seconds": 900,
            },
        }
    )

    assert config.database.path == tmp_path / "state.sqlite3"
    assert config.database.busy_timeout_ms == 12_000
    assert config.signoz.compose_directory == tmp_path / "signoz"
    assert config.scheduler.interval_seconds == 1_800
    assert config.scheduler.lease_seconds == 900


@pytest.mark.parametrize(
    "document, message",
    [
        ({"unexpected": {}}, "unsupported keys in root"),
        ({"database": []}, "database must be a TOML table"),
        ({"database": {"busy_timeout_ms": True}}, "must be a positive integer"),
        ({"database": {"path": "  "}}, "must be a non-empty string"),
        ({"signoz": {"docker_host": 42}}, "must be a non-empty string"),
        ({"scheduler": {"other": 1}}, "unsupported keys in scheduler"),
        ({"scheduler": {"interval_seconds": 0}}, "must be a positive integer"),
        ({"scheduler": {"interval_seconds": -1}}, "must be a positive integer"),
        ({"scheduler": {"interval_seconds": True}}, "must be a positive integer"),
        ({"scheduler": {"timezone": "Mars/Olympus"}}, "must name an installed timezone"),
    ],
)
def test_invalid_configuration_fails_closed(document: dict[str, object], message: str) -> None:
    with pytest.raises(ConfigurationError, match=message):
        parse_config(document)


def test_load_config_distinguishes_default_absence_from_explicit_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    default_path = tmp_path / "default.toml"
    monkeypatch.setattr(config_module, "DEFAULT_CONFIG_PATH", default_path)

    assert load_config().database.busy_timeout_ms == 5_000
    with pytest.raises(ConfigurationError, match="does not exist"):
        load_config(tmp_path / "explicit.toml")


def test_load_config_rejects_invalid_toml(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[database\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="invalid TOML"):
        load_config(path)

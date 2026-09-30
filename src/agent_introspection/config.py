"""Typed application configuration loaded from TOML."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

DEFAULT_DATABASE_PATH: Final[Path] = Path(
    "~/.local/share/agent-introspection/introspection.sqlite3"
).expanduser()
DEFAULT_CONFIG_PATH: Final[Path] = Path("~/.config/agent-introspection/config.toml").expanduser()


class ConfigurationError(ValueError):
    """Raised when configuration is malformed or contains unsupported keys."""


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    """SQLite workflow store."""

    path: Path = DEFAULT_DATABASE_PATH
    busy_timeout_ms: int = 5_000


@dataclass(frozen=True, slots=True)
class SigNozConfig:
    """The SigNoz ClickHouse container that holds the facts store."""

    clickhouse_container: str = "signoz-clickhouse"
    docker_context: str = "orbstack"


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Complete application configuration."""

    database: DatabaseConfig = DatabaseConfig()
    signoz: SigNozConfig = SigNozConfig()


_ROOT_KEYS = frozenset({"database", "signoz"})
_DATABASE_KEYS = frozenset({"path", "busy_timeout_ms"})
_SIGNOZ_KEYS = frozenset({"clickhouse_container", "docker_context"})
# Keys the retired scan pipeline read. They are still rejected, so stale settings
# never look active, but the error names them as safe to delete.
_RETIRED_KEYS = {
    "configuration": frozenset({"scheduler", "lifecycle", "legacy_project_attribution"}),
    "signoz": frozenset(
        {
            "health_url",
            "otlp_http_endpoint",
            "compose_directory",
            "collector_container",
            "docker_host",
        }
    ),
}


def _expand_path(value: object, *, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{field} must be a non-empty string")
    expanded = os.path.expandvars(os.path.expanduser(value))
    return Path(expanded).resolve(strict=False)


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigurationError(f"{field} must be a positive integer")
    return value


def _non_empty_string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{field} must be a non-empty string")
    return value


def _table(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ConfigurationError(f"{name} must be a TOML table")
    return value


def _reject_unknown(actual: set[str], allowed: frozenset[str], *, location: str) -> None:
    unknown = sorted(actual - allowed)
    if not unknown:
        return
    retired = [key for key in unknown if key in _RETIRED_KEYS.get(location, frozenset())]
    message = f"unsupported keys in {location}: {', '.join(unknown)}"
    if retired:
        message += f" ({', '.join(retired)} retired with the scan pipeline; delete them)"
    raise ConfigurationError(message)


def parse_config(data: dict[str, Any]) -> AppConfig:
    """Validate a TOML document and return typed configuration."""
    _reject_unknown(set(data), _ROOT_KEYS, location="configuration")
    database = _table(data, "database")
    signoz = _table(data, "signoz")
    _reject_unknown(set(database), _DATABASE_KEYS, location="database")
    _reject_unknown(set(signoz), _SIGNOZ_KEYS, location="signoz")
    defaults = AppConfig()
    return AppConfig(
        database=DatabaseConfig(
            path=(
                _expand_path(database["path"], field="database.path")
                if "path" in database
                else defaults.database.path
            ),
            busy_timeout_ms=(
                _positive_int(database["busy_timeout_ms"], field="database.busy_timeout_ms")
                if "busy_timeout_ms" in database
                else defaults.database.busy_timeout_ms
            ),
        ),
        signoz=SigNozConfig(
            clickhouse_container=(
                _non_empty_string(
                    signoz["clickhouse_container"], field="signoz.clickhouse_container"
                )
                if "clickhouse_container" in signoz
                else defaults.signoz.clickhouse_container
            ),
            docker_context=(
                _non_empty_string(signoz["docker_context"], field="signoz.docker_context")
                if "docker_context" in signoz
                else defaults.signoz.docker_context
            ),
        ),
    )


def load_config(path: Path | None = None) -> AppConfig:
    """Load configuration from ``path`` or the canonical user configuration path.

    An absent canonical configuration file means the documented defaults are used. An
    explicitly supplied path must exist.
    """
    config_path = (
        path.expanduser().resolve(strict=False) if path is not None else DEFAULT_CONFIG_PATH
    )
    if not config_path.exists():
        if path is not None:
            raise ConfigurationError(f"configuration file does not exist: {config_path}")
        return AppConfig()
    if not config_path.is_file():
        raise ConfigurationError(f"configuration path is not a file: {config_path}")
    try:
        with config_path.open("rb") as handle:
            document = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"invalid TOML in {config_path}: {exc}") from exc
    except OSError as exc:
        raise ConfigurationError(f"configuration file cannot be read: {config_path}") from exc
    return parse_config(document)

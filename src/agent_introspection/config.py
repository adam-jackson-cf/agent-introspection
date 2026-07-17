"""Typed application configuration loaded from TOML."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_DATABASE_PATH: Final[Path] = Path(
    "~/.local/share/agent-introspection/introspection.sqlite3"
).expanduser()
DEFAULT_CONFIG_PATH: Final[Path] = Path("~/.config/agent-introspection/config.toml").expanduser()
DEFAULT_COMPOSE_DIRECTORY: Final[Path] = Path(
    "~/.local/share/codex-observability/signoz/deploy/docker"
).expanduser()


class ConfigurationError(ValueError):
    """Raised when configuration is malformed or contains unsupported keys."""


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    """SQLite configuration."""

    path: Path = DEFAULT_DATABASE_PATH
    busy_timeout_ms: int = 5_000


@dataclass(frozen=True, slots=True)
class SigNozConfig:
    """Local SigNoz endpoints and Compose location."""

    health_url: str = "http://localhost:8080/api/v1/health"
    otlp_http_endpoint: str = "http://localhost:4318"
    compose_directory: Path = DEFAULT_COMPOSE_DIRECTORY
    clickhouse_container: str = "signoz-clickhouse"
    collector_container: str = "signoz-otel-collector"
    docker_context: str = "orbstack"
    docker_host: str = "unix:///Users/adamjackson/.orbstack/run/docker.sock"


@dataclass(frozen=True, slots=True)
class SchedulerConfig:
    """Hourly slot and lease settings."""

    timezone: str = "Europe/London"
    interval_seconds: int = 3_600
    lease_seconds: int = 3_600


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Complete application configuration."""

    database: DatabaseConfig = DatabaseConfig()
    signoz: SigNozConfig = SigNozConfig()
    scheduler: SchedulerConfig = SchedulerConfig()


_ROOT_KEYS = frozenset({"database", "signoz", "scheduler"})
_DATABASE_KEYS = frozenset({"path", "busy_timeout_ms"})
_SIGNOZ_KEYS = frozenset(
    {
        "health_url",
        "otlp_http_endpoint",
        "compose_directory",
        "clickhouse_container",
        "collector_container",
        "docker_context",
        "docker_host",
    }
)
_SCHEDULER_KEYS = frozenset({"timezone", "interval_seconds", "lease_seconds"})


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


def _timezone(value: object, *, field: str) -> str:
    name = _non_empty_string(value, field=field)
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ConfigurationError(f"{field} must name an installed timezone") from exc
    return name


def _table(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ConfigurationError(f"{name} must be a TOML table")
    return value


def _reject_unknown(actual: set[str], allowed: frozenset[str], *, location: str) -> None:
    unknown = sorted(actual - allowed)
    if unknown:
        raise ConfigurationError(f"unsupported keys in {location}: {', '.join(unknown)}")


def parse_config(data: dict[str, Any]) -> AppConfig:
    """Validate parsed TOML and construct the typed configuration."""

    _reject_unknown(set(data), _ROOT_KEYS, location="root")
    database = _table(data, "database")
    signoz = _table(data, "signoz")
    scheduler = _table(data, "scheduler")
    _reject_unknown(set(database), _DATABASE_KEYS, location="database")
    _reject_unknown(set(signoz), _SIGNOZ_KEYS, location="signoz")
    _reject_unknown(set(scheduler), _SCHEDULER_KEYS, location="scheduler")

    defaults = AppConfig()
    database_config = DatabaseConfig(
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
    )
    signoz_config = SigNozConfig(
        health_url=(
            _non_empty_string(signoz["health_url"], field="signoz.health_url")
            if "health_url" in signoz
            else defaults.signoz.health_url
        ),
        otlp_http_endpoint=(
            _non_empty_string(signoz["otlp_http_endpoint"], field="signoz.otlp_http_endpoint")
            if "otlp_http_endpoint" in signoz
            else defaults.signoz.otlp_http_endpoint
        ),
        compose_directory=(
            _expand_path(signoz["compose_directory"], field="signoz.compose_directory")
            if "compose_directory" in signoz
            else defaults.signoz.compose_directory
        ),
        clickhouse_container=(
            _non_empty_string(signoz["clickhouse_container"], field="signoz.clickhouse_container")
            if "clickhouse_container" in signoz
            else defaults.signoz.clickhouse_container
        ),
        collector_container=(
            _non_empty_string(signoz["collector_container"], field="signoz.collector_container")
            if "collector_container" in signoz
            else defaults.signoz.collector_container
        ),
        docker_context=(
            _non_empty_string(signoz["docker_context"], field="signoz.docker_context")
            if "docker_context" in signoz
            else defaults.signoz.docker_context
        ),
        docker_host=(
            _non_empty_string(signoz["docker_host"], field="signoz.docker_host")
            if "docker_host" in signoz
            else defaults.signoz.docker_host
        ),
    )
    scheduler_config = SchedulerConfig(
        timezone=(
            _timezone(scheduler["timezone"], field="scheduler.timezone")
            if "timezone" in scheduler
            else defaults.scheduler.timezone
        ),
        interval_seconds=(
            _positive_int(scheduler["interval_seconds"], field="scheduler.interval_seconds")
            if "interval_seconds" in scheduler
            else defaults.scheduler.interval_seconds
        ),
        lease_seconds=(
            _positive_int(scheduler["lease_seconds"], field="scheduler.lease_seconds")
            if "lease_seconds" in scheduler
            else defaults.scheduler.lease_seconds
        ),
    )
    return AppConfig(database=database_config, signoz=signoz_config, scheduler=scheduler_config)


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

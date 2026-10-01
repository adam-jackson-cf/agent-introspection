"""Typed application configuration loaded from TOML."""

from __future__ import annotations

import ipaddress
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal
from urllib.parse import urlsplit

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
    """How to reach the SigNoz ClickHouse that holds the facts store.

    Agent Introspection runs only against a SigNoz installed on this machine. The
    docker mode (the default) runs ``clickhouse-client`` inside its ClickHouse
    container, through ``docker_context`` when set, else the current Docker context.
    The HTTP mode, selected by ``clickhouse_url``, talks to its ClickHouse over the HTTP
    interface at a local address (loopback, localhost, or a local container name such
    as OrbStack's *.orb.local). A password is read from the environment variable named
    by ``clickhouse_password_env``, or printed by ``clickhouse_password_command`` (an
    argv list such as a Keychain lookup, which also works for the launchd job), and is
    never stored here. ``otlp_endpoint`` records the collector producers export to, for
    docs and checks.
    """

    clickhouse_container: str = "signoz-clickhouse"
    docker_context: str | None = None
    clickhouse_url: str | None = None
    clickhouse_user: str = "default"
    clickhouse_password_env: str | None = None
    clickhouse_password_command: tuple[str, ...] | None = None
    otlp_endpoint: str | None = None

    @property
    def mode(self) -> Literal["docker", "http"]:
        """``http`` when a ClickHouse URL is configured, else ``docker``."""
        return "http" if self.clickhouse_url is not None else "docker"


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Complete application configuration."""

    database: DatabaseConfig = DatabaseConfig()
    signoz: SigNozConfig = SigNozConfig()
    dashboard_clickhouse_url: str | None = None


_ROOT_KEYS = frozenset({"database", "signoz", "dashboard"})
_DASHBOARD_KEYS = frozenset({"clickhouse_url"})
_DATABASE_KEYS = frozenset({"path", "busy_timeout_ms"})
_DOCKER_KEYS = frozenset({"clickhouse_container", "docker_context"})
_HTTP_KEYS = frozenset({
    "clickhouse_url",
    "clickhouse_user",
    "clickhouse_password_env",
    "clickhouse_password_command",
})
_SIGNOZ_KEYS = _DOCKER_KEYS | _HTTP_KEYS | {"otlp_endpoint"}
# Keys the retired scan pipeline read. They are still rejected, so stale settings
# never look active, but the error names them as safe to delete.
_RETIRED_KEYS = {
    "configuration": frozenset({"scheduler", "lifecycle", "legacy_project_attribution"}),
    "signoz": frozenset({
        "health_url",
        "otlp_http_endpoint",
        "compose_directory",
        "collector_container",
        "docker_host",
    }),
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


def _http_url(value: object, *, field: str) -> str:
    url = _non_empty_string(value, field=field).rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigurationError(f"{field} must be an http:// or https:// URL")
    if parts.username is not None or parts.password is not None:
        raise ConfigurationError(f"{field} must not embed credentials; use clickhouse_user")
    if not is_local_host(parts.hostname):
        raise ConfigurationError(
            f"{field} must point at this machine (loopback, localhost, or a local "
            "container name such as OrbStack's *.orb.local): "
            "Agent Introspection runs only against a local SigNoz"
        )
    return url


def is_local_host(host: str) -> bool:
    """Return whether ``host`` names this machine.

    Loopback addresses and ``localhost`` always do; ``*.orb.local`` is OrbStack's
    container DNS, which resolves only on this Mac. Other container runtimes publish
    ClickHouse on a loopback port instead.
    """
    host = host.lower().rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".orb.local")):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _signoz(signoz: dict[str, Any]) -> SigNozConfig:
    """Validate the [signoz] table: docker keys and HTTP keys are mutually exclusive."""
    keys = set(signoz)
    if keys & _DOCKER_KEYS and keys & _HTTP_KEYS:
        raise ConfigurationError(
            "signoz: configure exactly one connection mode, docker "
            "(clickhouse_container, docker_context) or http (clickhouse_url, ...)"
        )
    if keys & _HTTP_KEYS and "clickhouse_url" not in keys:
        raise ConfigurationError(
            "signoz: clickhouse_user and clickhouse_password_env need clickhouse_url"
        )
    defaults = SigNozConfig()
    text = {
        key: _non_empty_string(signoz[key], field=f"signoz.{key}")
        for key in ("clickhouse_container", "docker_context", "clickhouse_user")
        if key in signoz
    }
    password_env = signoz.get("clickhouse_password_env")
    if password_env is not None and (
        not isinstance(password_env, str) or not password_env.isidentifier()
    ):
        raise ConfigurationError(
            "signoz.clickhouse_password_env must name an environment variable, not hold a secret"
        )
    command = signoz.get("clickhouse_password_command")
    if command is not None and (
        not isinstance(command, list)
        or not command
        or not all(isinstance(part, str) and part for part in command)
    ):
        raise ConfigurationError(
            "signoz.clickhouse_password_command must be a non-empty list of arguments"
        )
    if command is not None and password_env is not None:
        raise ConfigurationError(
            "signoz: set clickhouse_password_env or clickhouse_password_command, not both"
        )
    return SigNozConfig(
        clickhouse_container=text.get("clickhouse_container", defaults.clickhouse_container),
        docker_context=text.get("docker_context"),
        clickhouse_url=(
            _http_url(signoz["clickhouse_url"], field="signoz.clickhouse_url")
            if "clickhouse_url" in signoz
            else None
        ),
        clickhouse_user=text.get("clickhouse_user", defaults.clickhouse_user),
        clickhouse_password_env=password_env,
        clickhouse_password_command=tuple(command) if command is not None else None,
        otlp_endpoint=(
            _http_url(signoz["otlp_endpoint"], field="signoz.otlp_endpoint")
            if "otlp_endpoint" in signoz
            else None
        ),
    )


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
    dashboard = _table(data, "dashboard")
    _reject_unknown(set(database), _DATABASE_KEYS, location="database")
    _reject_unknown(set(signoz), _SIGNOZ_KEYS, location="signoz")
    _reject_unknown(set(dashboard), _DASHBOARD_KEYS, location="dashboard")
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
        signoz=_signoz(signoz),
        dashboard_clickhouse_url=(
            _http_url(dashboard["clickhouse_url"], field="dashboard.clickhouse_url")
            if "clickhouse_url" in dashboard
            else None
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

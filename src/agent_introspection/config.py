"""Typed application configuration loaded from TOML."""

from __future__ import annotations

import ipaddress
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Literal
from urllib.parse import urlsplit

DEFAULT_DATABASE_PATH: Final[Path] = Path(
    "~/.local/share/agent-introspection/introspection.sqlite3"
).expanduser()
DEFAULT_CONFIG_PATH: Final[Path] = Path("~/.config/agent-introspection/config.toml").expanduser()
# The collector's OTLP ports. A collector endpoint harnesses export to is OTLP/HTTP.
OTLP_HTTP_PORT: Final = 4318
OTLP_GRPC_PORT: Final = 4317


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

    Agent Introspection runs only against a SigNoz installed on this machine, the host
    (an additional machine has none and uses none of these settings). The docker mode
    (the default) runs ``clickhouse-client`` inside its ClickHouse container, through
    ``docker_context`` when set, else the current Docker context.
    The HTTP mode, selected by ``clickhouse_url``, talks to its ClickHouse over the HTTP
    interface at a local address (loopback, localhost, or a local container name such
    as OrbStack's *.orb.local). A password is read from the environment variable named
    by ``clickhouse_password_env``, or printed by ``clickhouse_password_command`` (an
    argv list such as a Keychain lookup, which also works for the launchd job), and is
    never stored here. ``otlp_endpoint`` records the local collector the host's
    producers export to, an OTLP/HTTP base URL, for ``harness configure`` and checks.
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


class MachineRole(StrEnum):
    """What this machine does: run SigNoz and the facts store, or only send to them."""

    HOST = "host"
    ADDITIONAL = "additional"


@dataclass(frozen=True, slots=True)
class MachineConfig:
    """The role ``[machine]`` gives this machine; absent means the SigNoz host.

    An additional machine runs harnesses but has no SigNoz: its harnesses export to the
    host's collector at ``export_endpoint`` (an OTLP/HTTP base URL, the one setting that
    may name another machine), and its records go to ``[hooks] otlp_logs_endpoint``.
    The host's own harnesses export to ``[signoz] otlp_endpoint`` instead.
    """

    role: MachineRole = MachineRole.HOST
    export_endpoint: str | None = None

    @property
    def additional(self) -> bool:
        """Whether this is an additional machine, without SigNoz."""
        return self.role is MachineRole.ADDITIONAL


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Complete application configuration."""

    database: DatabaseConfig = DatabaseConfig()
    signoz: SigNozConfig = SigNozConfig()
    machine: MachineConfig = MachineConfig()
    dashboard_clickhouse_url: str | None = None
    # Registry harness keys this machine uses (`codex` names the three Codex surfaces);
    # None means every registry harness. `facts install` validates the keys.
    harnesses_enabled: tuple[str, ...] | None = None
    # OTLP/HTTP logs URL every record is sent to (`[hooks] otlp_logs_endpoint`); any
    # reachable host. None falls back to the OTEL_* environment, else spool only.
    hooks_otlp_logs_endpoint: str | None = None
    # Command that prints the OpenRouter key for prompt labels (`[labels]
    # openrouter_key_command`), an argv list such as a Keychain lookup that the scheduled
    # sync can also run. The key itself is never stored here.
    labels_openrouter_key_command: tuple[str, ...] | None = None

    @property
    def collector_endpoint(self) -> str | None:
        """The collector this machine's harnesses export to, as an OTLP base URL.

        ``[machine] export_endpoint`` on an additional machine, else ``[signoz]
        otlp_endpoint``; None means the local default (``profiles.DEFAULT_OTLP_ENDPOINT``).
        """
        if self.machine.additional:
            return self.machine.export_endpoint
        return self.signoz.otlp_endpoint


_ROOT_KEYS = frozenset({
    "database",
    "signoz",
    "dashboard",
    "harnesses",
    "hooks",
    "labels",
    "machine",
})
_MACHINE_KEYS = frozenset({"role", "export_endpoint"})
_HOOKS_KEYS = frozenset({"otlp_logs_endpoint"})
_LABELS_KEYS = frozenset({"openrouter_key_command"})
_HARNESSES_KEYS = frozenset({"enabled"})
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
# Keys the retired scan pipeline and project attribution read. They are still
# rejected, so stale settings never look active, but the error names them as safe to
# delete.
_RETIRED_KEYS = {
    "configuration": frozenset({
        "scheduler",
        "lifecycle",
        "legacy_project_attribution",
        "sessions",
    }),
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


def _argv(value: object, *, field: str) -> tuple[str, ...]:
    """Validate a command given as a non-empty list of non-empty argument strings."""
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(part, str) and part for part in value)
    ):
        raise ConfigurationError(f"{field} must be a non-empty list of arguments")
    return tuple(value)


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


def _otlp_url(value: object, *, field: str) -> str:
    """Validate an OTLP/HTTP URL: http(s), a host, no credentials; any host is allowed."""
    url = _non_empty_string(value, field=field).strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigurationError(f"{field} must be an http:// or https:// URL")
    if parts.username is not None or parts.password is not None:
        raise ConfigurationError(f"{field} must not embed credentials")
    return url


def _collector_url(url: str, *, field: str) -> str:
    """Refuse a collector endpoint on the OTLP/gRPC port.

    ``[signoz] otlp_endpoint`` and ``[machine] export_endpoint`` are OTLP/HTTP base URLs:
    every harness profile writes them with an OTLP/HTTP exporter, so a gRPC port would
    leave each harness posting OTLP/HTTP to a gRPC listener.
    """
    try:
        port = urlsplit(url).port
    except ValueError:
        raise ConfigurationError(f"{field} must name a valid port") from None
    if port == OTLP_GRPC_PORT:
        raise ConfigurationError(
            f"{field} must be the collector's OTLP/HTTP base URL (port {OTLP_HTTP_PORT}), "
            f"not its OTLP/gRPC port {OTLP_GRPC_PORT}: every harness exports over OTLP/HTTP"
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


def require_host(config: AppConfig, need: str) -> None:
    """Raise unless this machine is the SigNoz host; ``need`` says what needs the host."""
    if config.machine.additional:
        raise ConfigurationError(
            f'{need}, which this additional machine ([machine] role = "additional") does '
            "not have: run the command on the host. Here, `agent-introspection spool sync` "
            "sends this machine's records and `agent-introspection doctor` checks its setup"
        )


def _machine(machine: dict[str, Any]) -> MachineConfig:
    """Validate [machine]: an additional machine names the host collector it exports to."""
    role_value = machine.get("role", MachineRole.HOST.value)
    if role_value not in tuple(MachineRole):
        raise ConfigurationError('machine.role must be "host" or "additional"')
    role = MachineRole(role_value)
    if role is MachineRole.HOST:
        if "export_endpoint" in machine:
            raise ConfigurationError(
                'machine.export_endpoint applies only to role = "additional"; the host\'s '
                "harnesses export to [signoz] otlp_endpoint"
            )
        return MachineConfig()
    if "export_endpoint" not in machine:
        raise ConfigurationError(
            'machine.export_endpoint is required for role = "additional": the host '
            "collector's OTLP/HTTP base URL, such as http://<host>:4318"
        )
    field = "machine.export_endpoint"
    endpoint = _collector_url(_otlp_url(machine["export_endpoint"], field=field), field=field)
    return MachineConfig(role=role, export_endpoint=endpoint.rstrip("/"))


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
    command = (
        _argv(signoz["clickhouse_password_command"], field="signoz.clickhouse_password_command")
        if "clickhouse_password_command" in signoz
        else None
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
        clickhouse_password_command=command,
        otlp_endpoint=(
            _collector_url(
                _http_url(signoz["otlp_endpoint"], field="signoz.otlp_endpoint"),
                field="signoz.otlp_endpoint",
            )
            if "otlp_endpoint" in signoz
            else None
        ),
    )


def _enabled_harnesses(harnesses: dict[str, Any]) -> tuple[str, ...] | None:
    """Validate [harnesses] enabled: a non-empty list of distinct harness keys."""
    if "enabled" not in harnesses:
        return None
    value = harnesses["enabled"]
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(key, str) and key.strip() for key in value)
    ):
        raise ConfigurationError("harnesses.enabled must be a non-empty list of harness keys")
    if len(set(value)) != len(value):
        raise ConfigurationError("harnesses.enabled lists a harness more than once")
    return tuple(value)


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
        message += f" ({', '.join(retired)} retired; delete them)"
    raise ConfigurationError(message)


def parse_config(data: dict[str, Any]) -> AppConfig:
    """Validate a TOML document and return typed configuration."""
    _reject_unknown(set(data), _ROOT_KEYS, location="configuration")
    database = _table(data, "database")
    signoz = _table(data, "signoz")
    dashboard = _table(data, "dashboard")
    harnesses = _table(data, "harnesses")
    _reject_unknown(set(database), _DATABASE_KEYS, location="database")
    _reject_unknown(set(signoz), _SIGNOZ_KEYS, location="signoz")
    _reject_unknown(set(dashboard), _DASHBOARD_KEYS, location="dashboard")
    _reject_unknown(set(harnesses), _HARNESSES_KEYS, location="harnesses")
    hooks = _table(data, "hooks")
    _reject_unknown(set(hooks), _HOOKS_KEYS, location="hooks")
    labels = _table(data, "labels")
    _reject_unknown(set(labels), _LABELS_KEYS, location="labels")
    machine = _table(data, "machine")
    _reject_unknown(set(machine), _MACHINE_KEYS, location="machine")
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
        machine=_machine(machine),
        dashboard_clickhouse_url=(
            _http_url(dashboard["clickhouse_url"], field="dashboard.clickhouse_url")
            if "clickhouse_url" in dashboard
            else None
        ),
        harnesses_enabled=_enabled_harnesses(harnesses),
        hooks_otlp_logs_endpoint=(
            _otlp_url(hooks["otlp_logs_endpoint"], field="hooks.otlp_logs_endpoint")
            if "otlp_logs_endpoint" in hooks
            else None
        ),
        labels_openrouter_key_command=(
            _argv(labels["openrouter_key_command"], field="labels.openrouter_key_command")
            if "openrouter_key_command" in labels
            else None
        ),
    )


_OTLP_LOGS_ENDPOINT = "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"
_OTLP_ENDPOINT = "OTEL_EXPORTER_OTLP_ENDPOINT"
_OTLP_LOGS_PROTOCOL = "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL"
_OTLP_PROTOCOL = "OTEL_EXPORTER_OTLP_PROTOCOL"


def resolve_otlp_logs_endpoint(
    configured: str | None, environ: Mapping[str, str] | None = None
) -> str | None:
    """Return the OTLP/HTTP logs URL records are sent to, or None when none applies.

    ``[hooks] otlp_logs_endpoint`` wins. Otherwise the standard OpenTelemetry variables
    apply, but only when the exporter protocol is unset or ``http/*``: records are sent
    as OTLP/HTTP JSON, so a gRPC endpoint (a port that speaks another protocol) is never
    used. An invalid environment value is ignored, never an error.
    """
    if configured is not None:
        return configured
    env = os.environ if environ is None else environ
    protocol = env.get(_OTLP_LOGS_PROTOCOL) or env.get(_OTLP_PROTOCOL) or ""
    if protocol and not protocol.startswith("http/"):
        return None
    candidate = env.get(_OTLP_LOGS_ENDPOINT)
    if not candidate and env.get(_OTLP_ENDPOINT):
        candidate = f"{env[_OTLP_ENDPOINT].rstrip('/')}/v1/logs"
    if not candidate:
        return None
    try:
        return _otlp_url(candidate, field=_OTLP_LOGS_ENDPOINT)
    except ConfigurationError:
        return None


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

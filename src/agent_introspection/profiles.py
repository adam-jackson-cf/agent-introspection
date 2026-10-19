"""Harness profiles: how to switch on a producer's telemetry, and what it offers.

Each producer has one profile, a TOML file in the ``harness_profiles`` package
(schema ``agent-introspection.harness-profile/1``). A registered profile covers one or
more registry harnesses (``codex`` covers the three Codex surfaces); a candidate
describes a producer that is not a harness yet, from measured evidence. A profile
names the config files and settings that switch on its export, the session store and
activity-hook adapter it relies on, and a status for every producer capability
(``capabilities.py``), with per-surface overrides.
"""

from __future__ import annotations

import glob
import re
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from agent_introspection import capabilities, facts, hooks
from agent_introspection.capabilities import CapabilityClaim
from agent_introspection.config import (
    DEFAULT_OTLP_ENDPOINT,
    OTLP_GRPC_PORT,
    OTLP_HTTP_PORT,
    is_local_host,
)

SCHEMA = "agent-introspection.harness-profile/1"
PACKAGE = "agent_introspection.harness_profiles"
PROFILE_STATUSES = ("registered", "candidate")
FILE_FORMATS = ("json", "toml", "dotenv")
# first: the first path whose variables are set; each: every root that is chosen or
# exists (Codex homes). See `config_paths`.
ROOT_MODES = ("first", "each")
SETTING_GROUPS = ("export", "prompt_export")
# Placeholders a setting value may hold. `service_name` is the profile's first one.
TEMPLATES = ("otlp_endpoint", "otlp_protocol", "service_name")
_TEMPLATE = re.compile(r"\{(\w+)\}")
# `$NAME` or `${NAME}` in a config file path.
_PATH_VARIABLE = re.compile(r"\$(?:\{(\w+)\}|(\w+))")
_PROFILE_KEYS = frozenset({
    "schema",
    "key",
    "title",
    "status",
    "registry_keys",
    "service_names",
    "binary",
    "version_args",
    "verified_versions",
    "hook_adapter",
    "sources",
    "config_file",
    "setting",
    "capabilities",
    "capability_overrides",
})
_CONFIG_FILE_KEYS = frozenset({"id", "paths", "format", "table", "roots", "home_globs"})
_SETTING_KEYS = frozenset({"file", "key", "value", "group", "purpose"})
_CLAIM_KEYS = frozenset({"status", "route", "evidence", "lacks"})
# The local collector's OTLP port for each transport, and the transport of each
# OTEL_EXPORTER_OTLP_PROTOCOL value.
OTLP_PORTS = {"http": OTLP_HTTP_PORT, "grpc": OTLP_GRPC_PORT}
OTLP_PROTOCOLS = {"http/protobuf": "http", "http/json": "http", "grpc": "grpc"}
# The protocol `{otlp_protocol}` renders to: the collector endpoint is OTLP/HTTP.
OTLP_HTTP_PROTOCOL = "http/protobuf"
# Codex exporter tables: `otlp-http = { endpoint, protocol }` posts to its endpoint as
# given, with protocol binary or json; `otlp-grpc = { endpoint }`.
_EXPORTER_TRANSPORTS = {"otlp-http": "http", "otlp-grpc": "grpc"}
_EXPORTER_HTTP_PROTOCOLS = ("binary", "json")
_TRANSPORT_TEMPLATES = frozenset({"otlp_endpoint", "otlp_protocol"})
_ENDPOINT = "{otlp_endpoint}"
_PROTOCOL = "{otlp_protocol}"

type SettingValue = str | bool | int | dict[str, SettingValue]


class ProfileError(ValueError):
    """A harness profile is malformed or disagrees with the registry."""


@dataclass(frozen=True)
class ConfigFile:
    """A file holding a producer's telemetry settings, and where in it they go."""

    id: str
    paths: tuple[str, ...]
    format: str
    table: str
    roots: str
    # Glob patterns of more config directories for an ``each`` file, such as Orca's
    # per-account Codex homes; ``~`` expands, and every matching directory is a root.
    home_globs: tuple[str, ...] = ()


@dataclass(frozen=True)
class Setting:
    """One managed key in a config file's settings table."""

    file: str
    key: str
    value: SettingValue
    group: str
    purpose: str

    def render(self, context: Mapping[str, str]) -> SettingValue:
        """Return the value with each placeholder filled from ``context``."""
        return render_value(self.value, context)


@dataclass(frozen=True)
class Profile:
    """One producer's telemetry configuration and capability claims."""

    key: str
    title: str
    status: str
    registry_keys: tuple[str, ...]
    service_names: tuple[str, ...]
    binary: str
    version_args: tuple[str, ...]
    verified_versions: tuple[str, ...]
    hook_adapter: str
    capabilities: Mapping[str, CapabilityClaim]
    sources: tuple[str, ...] = ()
    config_files: tuple[ConfigFile, ...] = ()
    settings: tuple[Setting, ...] = ()
    capability_overrides: Mapping[str, Mapping[str, CapabilityClaim]] = field(default_factory=dict)

    @property
    def aliases(self) -> tuple[str, ...]:
        """The names that resolve to this profile: key, registry keys, and service names."""
        return tuple(dict.fromkeys((self.key, *self.registry_keys, *self.service_names)))

    def claims(self, registry_key: str = "") -> dict[str, CapabilityClaim]:
        """Return the capability claims, with ``registry_key``'s overrides applied."""
        return {**self.capabilities, **self.capability_overrides.get(registry_key, {})}


def expand_config_path(raw: str, environ: Mapping[str, str], home: Path) -> Path | None:
    """Expand one config file path: ``$NAME`` or ``${NAME}``, and a leading ``~``.

    The one expansion ``doctor`` and ``harness configure`` share for a profile's
    ``[[config_file]] paths`` and ``--root`` directories. A path naming a variable that
    is unset or empty is None, so a harness's config-directory variable falls through
    to its default path. ``~`` expands only as the path's first part, as a shell does,
    never inside a variable's value. Nothing is globbed: each path names one file or
    directory.
    """
    names = [braced or bare for braced, bare in _PATH_VARIABLE.findall(raw)]
    if any(not environ.get(name) for name in names):
        return None
    text = _PATH_VARIABLE.sub(lambda match: environ[match.group(1) or match.group(2)], raw)
    if raw == "~" or raw.startswith("~/"):
        return home / text[2:]
    return Path(text)


def config_paths(
    config_file: ConfigFile, environ: Mapping[str, str], home: Path, extras: Sequence[Path] = ()
) -> list[Path]:
    """Return the files a profile's config file names on this machine, existing or not.

    The one resolution ``harness configure`` writes and ``doctor`` reads. A path whose
    variable is unset or empty is skipped (``expand_config_path``). A file configured
    once (``first``) is the first remaining path: a set config-directory variable
    replaces the default location, as it does for the harness (``CLAUDE_CONFIG_DIR``),
    even before the harness has written a file there. A file configured in each root
    (``each``) is in every remaining path that names a variable, the home the user
    chose, in every other whose directory exists, and in every directory a
    ``home_globs`` pattern matches. ``extras`` are more files of the same name in
    directories named by ``--root``: they replace a ``first`` file's path and join the
    ``each`` ones. With nothing chosen and no ``extras``, the first remaining path is,
    even when ``home_globs`` match: a matched home never stands in for the default one.
    """
    candidates = [
        (raw, path)
        for raw in config_file.paths
        if (path := expand_config_path(raw, environ, home)) is not None
    ]
    first = [path for _raw, path in candidates[:1]]
    if config_file.roots == "first":
        return list(extras) or first
    chosen = [
        path for raw, path in candidates if _PATH_VARIABLE.search(raw) or path.parent.is_dir()
    ]
    base = chosen or ([] if extras else first)
    return [*base, *_glob_homes(config_file, home), *extras]


def _glob_homes(config_file: ConfigFile, home: Path) -> list[Path]:
    """Return the file in every directory a ``home_globs`` pattern matches, sorted."""
    name = Path(config_file.paths[0]).name
    patterns = [
        str(home / pattern[2:]) if pattern.startswith("~/") else pattern
        for pattern in config_file.home_globs
    ]
    directories = {Path(match) for pattern in patterns for match in glob.glob(pattern)}
    return [directory / name for directory in sorted(directories) if directory.is_dir()]


def render_value(value: SettingValue, context: Mapping[str, str]) -> SettingValue:
    """Return ``value`` with each placeholder replaced from ``context``.

    Raises
    ------
    ProfileError
        When ``context`` has no value for a placeholder.
    """
    if isinstance(value, dict):
        return {key: render_value(item, context) for key, item in value.items()}
    if not isinstance(value, str):
        return value
    absent = sorted(set(_TEMPLATE.findall(value)) - set(context))
    _require(not absent, f"no value for {', '.join(absent)}")
    return _TEMPLATE.sub(lambda match: context[match.group(1)], value)


def export_context(profile: Profile, otlp_endpoint: str | None = None) -> dict[str, str]:
    """Return the values a profile's setting placeholders take on this machine.

    ``otlp_endpoint`` is the collector's OTLP/HTTP base URL,
    ``AppConfig.collector_endpoint``: config ``[signoz] otlp_endpoint`` (default
    ``DEFAULT_OTLP_ENDPOINT``), or on an additional machine ``[machine] export_endpoint``,
    the host's collector. Config refuses the OTLP/gRPC port there, so every profile
    writes one transport: the protocol is always ``http/protobuf``. The service name is
    the profile's first.
    """
    return {
        "otlp_endpoint": (otlp_endpoint or DEFAULT_OTLP_ENDPOINT).rstrip("/"),
        "otlp_protocol": OTLP_HTTP_PROTOCOL,
        "service_name": profile.service_names[0],
    }


def _placeholders(value: SettingValue) -> set[str]:
    if isinstance(value, dict):
        return {name for item in value.values() for name in _placeholders(item)}
    return set(_TEMPLATE.findall(value)) if isinstance(value, str) else set()


def _port(url: object) -> int | None:
    """Return the port an http(s) URL names, or None when it names none or is not one."""
    if not isinstance(url, str):
        return None
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        return None
    return port if parts.scheme in ("http", "https") and parts.hostname else None


def on_collector_host(url: str, collector: str) -> bool:
    """Return whether ``url`` names the machine of the collector at ``collector``.

    Every local name (``config.is_local_host``) is one machine, so a local collector is
    reached through any of them. A collector on another machine, the host an additional
    machine exports to, is reached only through the host name its own URL gives.
    """
    host = (urlsplit(url).hostname or "").rstrip(".")
    collector_host = (urlsplit(collector).hostname or "").rstrip(".")
    if is_local_host(collector_host):
        return is_local_host(host)
    return host == collector_host


def _collector_port(transport: str, context: Mapping[str, str]) -> int:
    """Return the port the collector serves ``transport`` on.

    OTLP/HTTP is served on the configured endpoint's port; OTLP/gRPC on OTLP's own.
    """
    configured = _port(context["otlp_endpoint"]) if transport == "http" else None
    return configured if configured is not None else OTLP_PORTS[transport]


def _reaches(url: object, transport: str, path: str, context: Mapping[str, str]) -> bool:
    """Return whether ``url`` is an exporter endpoint of the collector's ``transport``.

    It must name the collector's machine (``on_collector_host``) and port. OTLP/HTTP
    must post to the same path as the profile's value; gRPC takes no path.
    """
    port = _port(url)
    if port is None or not on_collector_host(cast(str, url), context["otlp_endpoint"]):
        return False
    if port != _collector_port(transport, context):
        return False
    actual = urlsplit(cast(str, url)).path.rstrip("/")
    return actual == (path.rstrip("/") if transport == "http" else "")


def _exporter_transport(
    rendered: SettingValue, value: object, context: Mapping[str, str]
) -> str | None:
    """Judge a Codex exporter table: one `otlp-http` or `otlp-grpc` entry."""
    if not isinstance(value, Mapping) or len(value) != 1:
        return None
    kind, options = next(iter(cast(Mapping[str, object], value).items()))
    transport = _EXPORTER_TRANSPORTS.get(kind)
    if transport is None or not isinstance(options, Mapping):
        return None
    wanted = next(iter(rendered.values()), {}) if isinstance(rendered, dict) else {}
    endpoint = wanted.get("endpoint") if isinstance(wanted, dict) else None
    path = urlsplit(endpoint).path if isinstance(endpoint, str) else ""
    protocol_ok = transport == "grpc" or options.get("protocol") in _EXPORTER_HTTP_PROTOCOLS
    reached = _reaches(options.get("endpoint"), transport, path, context)
    return transport if protocol_ok and reached else None


def _sibling(
    profile: Profile, setting: Setting, wanted: Callable[[SettingValue], bool]
) -> Setting | None:
    return next(
        (item for item in profile.settings if item.file == setting.file and wanted(item.value)),
        None,
    )


def _variable_transport(
    profile: Profile, setting: Setting, current: Mapping[str, object], context: Mapping[str, str]
) -> str | None:
    """Judge an endpoint or protocol variable with its partner: the exporter reads both.

    The endpoint keeps the current protocol when the profile accepts it (any OTLP
    protocol for a ``{otlp_protocol}`` value, else that literal), or else the one about
    to be written. A current protocol is kept when it reaches the collector with the
    current endpoint, or with the profile's endpoint that replaces it.
    """
    is_endpoint = isinstance(setting.value, str) and _ENDPOINT in setting.value
    endpoint = (
        setting
        if is_endpoint
        else _sibling(profile, setting, lambda value: isinstance(value, str) and _ENDPOINT in value)
    )
    protocol = _sibling(profile, setting, lambda value: value in (_PROTOCOL, *OTLP_PROTOCOLS))
    if endpoint is None:
        return None
    endpoint_value = cast(str, endpoint.render(context))
    path = urlsplit(endpoint_value).path
    current_protocol = current.get(protocol.key) if protocol is not None else None
    if is_endpoint:
        accepted = (
            OTLP_PROTOCOLS if protocol is None or protocol.value == _PROTOCOL else (protocol.value,)
        )
        chosen = (
            current_protocol
            if current_protocol in accepted
            else (protocol.render(context) if protocol is not None else context["otlp_protocol"])
        )
        transport = OTLP_PROTOCOLS.get(cast(str, chosen))
        reached = transport is not None and _reaches(
            current.get(endpoint.key), transport, path, context
        )
        return transport if reached else None
    transport = OTLP_PROTOCOLS.get(current_protocol) if isinstance(current_protocol, str) else None
    urls = (current.get(endpoint.key), endpoint_value)
    reached = transport is not None and any(_reaches(url, transport, path, context) for url in urls)
    return transport if reached else None


def equivalent_transport(
    profile: Profile, setting: Setting, current: Mapping[str, object], context: Mapping[str, str]
) -> str | None:
    """Return how an export setting's current value reaches the collector another way.

    The transport-equivalence rule shared by ``doctor`` and ``harness configure``: an
    export setting whose profile value templates the collector endpoint or protocol is
    satisfied by any OTLP exporter that reaches the collector, over OTLP/HTTP (port
    4318, or the configured endpoint's port; protocol ``http/protobuf`` or
    ``http/json``; a Codex ``otlp-http`` exporter posting to the same ``/v1/<signal>``
    path with protocol ``binary`` or ``json``) or over OTLP/gRPC (port 4317, protocol
    ``grpc``; a Codex ``otlp-grpc`` exporter). Its URL must name the collector's
    machine (``on_collector_host``): any local name for a local collector, the host's
    own name on an additional machine. ``current`` is the settings table as the file
    holds it now; ``context`` comes from ``export_context``.

    Returns
    -------
    str or None
        ``http`` or ``grpc``, or None when the setting is not templated that way or its
        current value is not such an exporter.
    """
    if setting.group != "export" or not _placeholders(setting.value) & _TRANSPORT_TEMPLATES:
        return None
    if isinstance(setting.value, dict):
        return _exporter_transport(setting.render(context), current.get(setting.key), context)
    return _variable_transport(profile, setting, current, context)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ProfileError(message)


def _text(table: Mapping[str, Any], key: str, where: str) -> str:
    value = table.get(key)
    _require(isinstance(value, str) and value.strip() != "", f"{where}: {key} must be text")
    return cast(str, value)


def _optional_text(table: Mapping[str, Any], key: str, where: str) -> str:
    value = table.get(key, "")
    _require(isinstance(value, str), f"{where}: {key} must be text")
    return cast(str, value)


def _texts(
    table: Mapping[str, Any], key: str, where: str, *, empty: bool = False
) -> tuple[str, ...]:
    value = table.get(key, [])
    _require(
        isinstance(value, list)
        and (empty or bool(value))
        and all(isinstance(item, str) and item != "" for item in value),
        f"{where}: {key} must be a {'' if empty else 'non-empty '}list of text",
    )
    return tuple(cast(list[str], value))


def _choice(table: Mapping[str, Any], key: str, choices: Sequence[str], where: str) -> str:
    value = table.get(key)
    _require(value in choices, f"{where}: {key} must be one of {', '.join(choices)}")
    return cast(str, value)


def _table(value: object, where: str, allowed: frozenset[str]) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), f"{where} must be a table")
    table = cast(Mapping[str, Any], value)
    unknown = sorted(set(table) - allowed)
    _require(not unknown, f"{where}: unknown key {', '.join(unknown)}")
    return table


def _tables(value: object, where: str, allowed: frozenset[str]) -> list[Mapping[str, Any]]:
    _require(isinstance(value, list), f"{where} must be an array of tables")
    return [_table(entry, where, allowed) for entry in cast(list[object], value)]


def _config_files(value: object, where: str) -> tuple[ConfigFile, ...]:
    config_files: list[ConfigFile] = []
    for entry in _tables(value, f"{where} config_file", _CONFIG_FILE_KEYS):
        file_id = _text(entry, "id", f"{where} config_file")
        label = f"{where} config_file {file_id}"
        _require(all(item.id != file_id for item in config_files), f"{label}: duplicate id")
        config = ConfigFile(
            id=file_id,
            paths=_texts(entry, "paths", label),
            format=_choice(entry, "format", FILE_FORMATS, label),
            table=_optional_text(entry, "table", label),
            roots=_choice(entry, "roots", ROOT_MODES, label),
            home_globs=_texts(entry, "home_globs", label, empty=True)
            if "home_globs" in entry
            else (),
        )
        _require(
            config.format != "dotenv" or config.table == "",
            f"{label}: a dotenv file has no tables",
        )
        _require(
            config.roots == "each" or not config.home_globs,
            f"{label}: home_globs needs roots = each",
        )
        config_files.append(config)
    return tuple(config_files)


def _setting_value(value: object, where: str) -> SettingValue:
    if isinstance(value, dict):
        return {str(key): _setting_value(item, f"{where}.{key}") for key, item in value.items()}
    _require(
        isinstance(value, str | bool | int),
        f"{where}: value must be text, a boolean, an integer, or a table of them",
    )
    if isinstance(value, str):
        unknown = sorted(set(_TEMPLATE.findall(value)) - set(TEMPLATES))
        _require(
            not unknown,
            f"{where}: unknown placeholder {', '.join(unknown)}; known: {', '.join(TEMPLATES)}",
        )
    return cast(SettingValue, value)


def _settings(value: object, config_files: Sequence[ConfigFile], where: str) -> tuple[Setting, ...]:
    file_ids = {config.id for config in config_files}
    settings: list[Setting] = []
    for entry in _tables(value, f"{where} setting", _SETTING_KEYS):
        key = _text(entry, "key", f"{where} setting")
        label = f"{where} setting {key}"
        file_id = _choice(entry, "file", sorted(file_ids), label)
        _require(
            all((item.file, item.key) != (file_id, key) for item in settings),
            f"{label}: set twice in {file_id}",
        )
        _require("value" in entry, f"{label}: value is required")
        settings.append(
            Setting(
                file=file_id,
                key=key,
                value=_setting_value(entry["value"], label),
                group=_choice(entry, "group", SETTING_GROUPS, label),
                purpose=_text(entry, "purpose", label),
            )
        )
    return tuple(settings)


def _claim(value: object, where: str) -> CapabilityClaim:
    table = _table(value, where, _CLAIM_KEYS)
    return CapabilityClaim(
        status=_choice(table, "status", capabilities.STATUSES, where),
        evidence=_text(table, "evidence", where),
        route=_optional_text(table, "route", where),
        lacks=_texts(table, "lacks", where, empty=True),
    )


def _claims(value: object, where: str, *, complete: bool) -> dict[str, CapabilityClaim]:
    table = _table(value, where, frozenset(capabilities.CAPABILITY_IDS))
    if complete:
        absent = [item for item in capabilities.CAPABILITY_IDS if item not in table]
        _require(not absent, f"{where}: no claim for {', '.join(absent)}")
    return {
        capability: _claim(table[capability], f"{where}.{capability}")
        for capability in capabilities.CAPABILITY_IDS
        if capability in table
    }


def _overrides(value: object, where: str) -> dict[str, dict[str, CapabilityClaim]]:
    _require(isinstance(value, Mapping), f"{where}: capability_overrides must be a table")
    return {
        str(surface): _claims(claims, f"{where} capability_overrides.{surface}", complete=False)
        for surface, claims in cast(Mapping[str, object], value).items()
    }


def _check_registry(profile: Profile, registry: facts.Registry, where: str) -> None:
    """Check the profile's harness identity and hook adapter against the registry."""
    if profile.status == "candidate":
        _require(not profile.registry_keys, f"{where}: a candidate covers no registry harness")
    else:
        unknown = [key for key in profile.registry_keys if key not in registry.harnesses]
        _require(
            bool(profile.registry_keys) and not unknown,
            f"{where}: registry_keys must name [harnesses] keys; unknown: {', '.join(unknown)}",
        )
        _require(
            set(profile.registry_keys) <= set(profile.service_names),
            f"{where}: a registry harness key is the service name its rows carry",
        )
        _require(
            any(setting.group == "export" for setting in profile.settings),
            f"{where}: a registered profile names the settings that switch on its export",
        )
    _require(
        profile.hook_adapter in ("", *hooks.PRODUCERS),
        f"{where}: hook_adapter must be empty or one of {', '.join(hooks.PRODUCERS)}",
    )
    stray = sorted(set(profile.capability_overrides) - set(profile.registry_keys))
    _require(not stray, f"{where}: overrides for unknown surface {', '.join(stray)}")


def _check_claim(
    profile: Profile, registry: facts.Registry, capability: str, claim: CapabilityClaim
) -> None:
    """Check one claim's route, hook status, and lacked signals against the registry."""
    where = f"profile {profile.key} {capability}"
    if claim.route:
        rows = [row for row in registry.routes if row["route"] == claim.route]
        _require(
            any(row["harness"] in profile.registry_keys for row in rows),
            f"{where}: route {claim.route} is not a registry route of this profile",
        )
        _require(
            rows[0]["source"] != "hooks" or claim.status == "hook",
            f"{where}: route {claim.route} comes from an activity hook, so the status is hook",
        )
    _require(
        claim.status != "hook" or profile.hook_adapter != "",
        f"{where}: a hook status needs a hook_adapter",
    )
    unrelated = [
        signal for signal in claim.lacks if capability not in registry.requires.get(signal, ())
    ]
    _require(not unrelated, f"{where}: lacks {', '.join(unrelated)}, which does not require it")


def parse_profile(
    document: Mapping[str, Any],
    registry: facts.Registry,
    where: str = "profile",
) -> Profile:
    """Validate one parsed profile against the contract and the registry.

    Raises
    ------
    ProfileError
        When a key is missing, unknown, or of the wrong type, or the profile names a
        harness, route, adapter, capability, or signal that does not exist.
    """
    _require(document.get("schema") == SCHEMA, f"{where}: schema must be {SCHEMA}")
    _table(document, where, _PROFILE_KEYS)
    config_files = _config_files(document.get("config_file", []), where)
    profile = Profile(
        key=_text(document, "key", where),
        title=_text(document, "title", where),
        status=_choice(document, "status", PROFILE_STATUSES, where),
        registry_keys=_texts(document, "registry_keys", where, empty=True),
        service_names=_texts(document, "service_names", where),
        binary=_text(document, "binary", where),
        version_args=_texts(document, "version_args", where, empty=True),
        verified_versions=_texts(document, "verified_versions", where),
        hook_adapter=_optional_text(document, "hook_adapter", where),
        capabilities=_claims(document.get("capabilities"), f"{where} capabilities", complete=True),
        sources=_texts(document, "sources", where, empty=True),
        config_files=config_files,
        settings=_settings(document.get("setting", []), config_files, where),
        capability_overrides=_overrides(document.get("capability_overrides", {}), where),
    )
    _check_registry(profile, registry, where)
    for surface in ("", *profile.capability_overrides):
        for capability, claim in profile.claims(surface).items():
            _check_claim(profile, registry, capability, claim)
    return profile


def _parse_text(text: str, registry: facts.Registry, where: str) -> Profile:
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ProfileError(f"{where}: {error}") from None
    return parse_profile(document, registry, where)


def load_profile(path: Path, registry: facts.Registry | None = None) -> Profile:
    """Load and validate one profile file, such as a draft for a candidate producer."""
    return _parse_text(path.read_text(), registry or facts.load_registry(), str(path))


def _check_profile_set(profiles: Sequence[Profile], registry: facts.Registry) -> None:
    """Check names are unambiguous and every registry harness has one registered profile."""
    names = [name for profile in profiles for name in profile.aliases]
    repeated = sorted({name for name in names if names.count(name) > 1})
    _require(not repeated, f"harness profiles: {', '.join(repeated)} name more than one")
    covered = [
        key
        for profile in profiles
        if profile.status == "registered"
        for key in profile.registry_keys
    ]
    uncovered = [harness for harness in registry.harnesses if harness not in covered]
    _require(not uncovered, f"harness profiles: no registered profile for {', '.join(uncovered)}")


def load_profiles(registry: facts.Registry | None = None) -> tuple[Profile, ...]:
    """Load and validate every bundled profile, ordered by key.

    Raises
    ------
    ProfileError
        When a profile is invalid, its file name is not ``<key>.toml``, two profiles
        share a name, or a registry harness has no registered profile.
    """
    registry = registry or facts.load_registry()
    profiles: list[Profile] = []
    for resource in sorted(files(PACKAGE).iterdir(), key=lambda item: item.name):
        if resource.name.endswith(".toml"):
            profile = _parse_text(resource.read_text(), registry, resource.name)
            _require(
                resource.name == f"{profile.key}.toml",
                f"{resource.name}: the file name must be {profile.key}.toml",
            )
            profiles.append(profile)
    _check_profile_set(profiles, registry)
    return tuple(profiles)


def resolve(name: str, profiles: Sequence[Profile] | None = None) -> Profile:
    """Return the profile a profile key, registry harness key, or service name names.

    Raises
    ------
    ProfileError
        When no profile has that name.
    """
    known = load_profiles() if profiles is None else profiles
    for profile in known:
        if name in profile.aliases:
            return profile
    names = ", ".join(alias for profile in known for alias in profile.aliases)
    raise ProfileError(f"unknown harness profile {name}; known: {names}")

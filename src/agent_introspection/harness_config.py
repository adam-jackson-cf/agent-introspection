from __future__ import annotations

import os
import shutil
import subprocess
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from agent_introspection import (
    capabilities,
    config_writers,
    doctor_harness,
    facts,
    hook_install,
    profiles,
)
from agent_introspection.profiles import ConfigFile, Profile, Setting, SettingValue

_SHOWN_PARTS = frozenset({"otlp-http", "otlp-grpc", "endpoint", "protocol"})
_NOT_SHOWN = "(not shown)"


@dataclass(frozen=True)
class ConfigureOptions:
    dry_run: bool = False
    prompt_export: bool = False
    force: bool = False
    roots: tuple[str, ...] = ()
    config_path: Path | None = None


class SettingChange(TypedDict):
    key: str
    group: str
    action: str
    old: object
    new: object
    transport: NotRequired[str]
    opt_in: NotRequired[bool]
    on: NotRequired[bool]


@dataclass(frozen=True)
class _Plan:
    changes: list[SettingChange]
    updates: dict[str, SettingValue]


def omp_plugin_installed(home: Path) -> bool:
    return (home / doctor_harness.OMP_PLUGIN_LINK).is_symlink()


def profile_keys(enabled: Sequence[str]) -> list[str]:
    known = profiles.load_profiles()
    return list(dict.fromkeys(profiles.resolve(name, known).key for name in enabled))


def harness_list(enabled: Sequence[str] | None, *, versions: bool = True) -> dict[str, Any]:
    registry = facts.load_registry()
    harnesses = facts.enabled_harnesses(registry, enabled)
    return {
        "enabled": list(harnesses),
        "harnesses": [
            _listing(profile, harnesses, versions) for profile in profiles.load_profiles(registry)
        ],
    }


def listed_version(profile: Profile, binary_path: str | None, run: bool) -> dict[str, Any]:
    unknown: dict[str, Any] = {"installed_version": None, "version_verified": None}
    if binary_path is None:
        return unknown
    command = " ".join((profile.binary, *profile.version_args))
    if not run or not profile.version_args:
        reason = "--no-version" if not run else "the profile names no version_args"
        return {**unknown, "version_note": f"not run ({reason})"}
    try:
        version = doctor_harness.installed_version([binary_path, *profile.version_args])
    except (OSError, subprocess.SubprocessError) as exc:
        return {**unknown, "version_note": f"`{command}` failed ({type(exc).__name__})"}
    if not version:
        return {**unknown, "version_note": f"`{command}` printed no version"}
    return {"installed_version": version, "version_verified": version in profile.verified_versions}


def _listing(profile: Profile, harnesses: Sequence[str], versions: bool) -> dict[str, Any]:
    enabled = [key for key in profile.registry_keys if key in harnesses]
    binary_path = shutil.which(profile.binary)
    return {
        "key": profile.key,
        "title": profile.title,
        "status": profile.status,
        "registry_keys": list(profile.registry_keys),
        "service_names": list(profile.service_names),
        "enabled": bool(enabled),
        "enabled_keys": enabled,
        "binary": profile.binary,
        "binary_found": binary_path is not None,
        "binary_path": binary_path,
        **listed_version(profile, binary_path, versions),
        "verified_versions": list(profile.verified_versions),
        "hook_adapter": profile.hook_adapter,
    }


def _expand(path: str) -> Path | None:
    return profiles.expand_config_path(path, os.environ, Path.home())


def _root_file(root: str, config_file: ConfigFile) -> Path:
    directory = _expand(root)
    if directory is None or not directory.is_dir():
        raise ValueError(f"--root {root} is not a directory")
    return directory / Path(config_file.paths[0]).name


def config_targets(config_file: ConfigFile, roots: Sequence[str] = ()) -> list[Path]:
    extras = [_root_file(root, config_file) for root in roots]
    chosen = profiles.config_paths(config_file, os.environ, Path.home(), extras)
    if not chosen:
        raise ValueError(
            f"{config_file.id}: no path to configure; set a variable in "
            + ", ".join(config_file.paths)
        )
    return list(dict.fromkeys(path.resolve() for path in chosen))


def _shown(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _shown(item) if key in _SHOWN_PARTS else _NOT_SHOWN
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_shown(item) for item in value]
    if isinstance(value, str):
        return doctor_harness.shown_text(value)
    return value


def _change(
    profile: Profile,
    setting: Setting,
    current: Mapping[str, object],
    context: Mapping[str, str],
    options: ConfigureOptions,
) -> tuple[SettingChange, SettingValue]:
    wanted = setting.render(context)
    old = current.get(setting.key)
    change: SettingChange = {
        "key": setting.key,
        "group": setting.group,
        "action": "change",
        "old": _shown(old),
        "new": _shown(wanted),
    }
    transport = profiles.equivalent_transport(profile, setting, current, context)
    if setting.group == "prompt_export" and not options.prompt_export:
        change["action"] = "unchanged"
        change["new"] = _shown(old)
        change["opt_in"] = True
        change["on"] = old == wanted
    elif setting.key not in current:
        change["action"] = "add"
    elif old == wanted:
        change["action"] = "unchanged"
    elif transport is not None and not options.force:
        change["action"] = "satisfied"
        change["new"] = _shown(old)
        change["transport"] = transport
    return change, wanted


def _plan(
    profile: Profile,
    config_file: ConfigFile,
    current: Mapping[str, object],
    options: ConfigureOptions,
    context: Mapping[str, str],
) -> _Plan:
    changes: list[SettingChange] = []
    updates: dict[str, SettingValue] = {}
    for setting in profile.settings:
        if setting.file == config_file.id:
            change, wanted = _change(profile, setting, current, context, options)
            changes.append(change)
            if change["action"] in ("add", "change"):
                updates[setting.key] = wanted
    return _Plan(changes, updates)


def _read(path: Path) -> str | None:
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError(f"{path} is not a file")
    return path.read_bytes().decode("utf-8")


def _configure_file(
    profile: Profile,
    config_file: ConfigFile,
    path: Path,
    options: ConfigureOptions,
    context: Mapping[str, str],
) -> dict[str, Any]:
    original = _read(path)
    text = original or ""
    try:
        current = config_writers.read_table(text, config_file.format, config_file.table)
        plan = _plan(profile, config_file, current, options, context)
        updated = (
            config_writers.update_table(text, config_file.format, config_file.table, plan.updates)
            if plan.updates
            else config_writers.TableUpdate(text)
        )
    except config_writers.ConfigFileError as error:
        raise ValueError(f"{path}: {error}") from None
    backup: Path | None = None
    write = bool(plan.updates) and not options.dry_run
    if write:
        mode = path.stat().st_mode & 0o777 if original is not None else config_writers.NEW_FILE_MODE
        backup = config_writers.backup(path) if original is not None else None
        config_writers.atomic_write(path, updated.text, mode)
    return {
        "id": config_file.id,
        "path": str(path),
        "format": config_file.format,
        "table": config_file.table,
        "exists": original is not None,
        "written": write,
        "backup": str(backup) if backup is not None else None,
        "notes": list(updated.notes),
        "changes": plan.changes,
    }


def _changed(items: Sequence[Mapping[str, Any]]) -> bool:
    return any(item.get("action") not in (None, "unchanged") for item in items)


def _hooks_changed(hooks: Mapping[str, Any]) -> bool:
    entries = [*hooks.get("runtime", []), *hooks.get("settings", []), hooks.get("plugin", {})]
    return _changed(entries)


def _next_steps(profile: Profile, changed: bool) -> list[str]:
    if not changed:
        return []
    return [f"Restart running {profile.title} sessions: they read these settings at start."]


def _registered(name: str) -> Profile:
    profile = profiles.resolve(name)
    if profile.status != "registered":
        raise ValueError(
            f"{profile.key} is a candidate producer, not a harness: it must first be added "
            "as a harness. Draft its profile with `agent-introspection producers profile "
            f"<service>` and preview the parity cost with `agent-introspection harness "
            f"impact {profile.key}`"
        )
    return profile


def _settings_files(profile: Profile, roots: Sequence[str]) -> list[Path]:
    return [
        path
        for config_file in profile.config_files
        if config_file.format == "json"
        for path in config_targets(config_file, roots)
    ]


def remove_hooks(name: str, *, dry_run: bool = False, roots: Sequence[str] = ()) -> dict[str, Any]:
    profile = _registered(name)
    options = hook_install.HookOptions(home=Path.home(), dry_run=dry_run)
    hooks = hook_install.remove(profile.hook_adapter, _settings_files(profile, roots), options)
    return {
        "harness": profile.key,
        "dry_run": dry_run,
        "hooks": hooks,
        "next_steps": _next_steps(profile, _hooks_changed(hooks)),
    }


def configure(name: str, otlp_endpoint: str | None, options: ConfigureOptions) -> dict[str, Any]:
    profile = _registered(name)
    context = profiles.export_context(profile, otlp_endpoint)
    hook_install.require(profile.hook_adapter)
    files = [
        _configure_file(profile, config_file, path, options, context)
        for config_file in profile.config_files
        for path in config_targets(config_file, options.roots)
    ]
    written = frozenset(Path(item["path"]).resolve() for item in files if item["written"])
    hooks = (
        hook_install.install(
            profile.hook_adapter,
            _settings_files(profile, options.roots),
            hook_install.HookOptions(
                home=Path.home(),
                dry_run=options.dry_run,
                backed_up=written,
                config_path=options.config_path,
            ),
        )
        if profile.hook_adapter
        else {}
    )
    changed = _hooks_changed(hooks) or any(
        change["action"] in ("add", "change") for item in files for change in item["changes"]
    )
    return {
        "harness": profile.key,
        "dry_run": options.dry_run,
        "prompt_export": options.prompt_export,
        "force": options.force,
        "otlp_endpoint": context["otlp_endpoint"],
        "files": files,
        "hooks": hooks,
        "next_steps": _next_steps(profile, changed),
    }


def _impact_profile(name: str, registry: facts.Registry) -> Profile:
    if name.endswith(".toml") or os.sep in name:
        path = Path(name).expanduser()
        if not path.is_file():
            raise ValueError(f"no profile file at {name}")
        return profiles.load_profile(path, registry)
    return profiles.resolve(name, profiles.load_profiles(registry))


def _projection(profile: Profile, registry: facts.Registry) -> dict[str, dict[str, int]]:
    surfaces = profile.registry_keys or ("",)
    summary: dict[str, dict[str, int]] = {}
    for surface in surfaces:
        counts = Counter(capabilities.project(profile, registry, surface).values())
        summary[surface or profile.key] = {
            outcome: counts[outcome] for outcome in capabilities.OUTCOMES
        }
    return summary


def harness_impact(name: str, enabled: Sequence[str] | None) -> dict[str, Any]:
    registry = facts.load_registry()
    profile = _impact_profile(name, registry)
    result: dict[str, Any] = {
        **capabilities.impact(enabled, profile, registry),
        "status": profile.status,
        "projection": _projection(profile, registry),
    }
    harnesses = facts.enabled_harnesses(registry, enabled)
    already = [key for key in profile.registry_keys if key in harnesses]
    if already:
        result["already_enabled"] = already
        result["note"] = (
            f"{profile.title} is already enabled; hidden_now lists the signals it keeps "
            "hidden on this machine"
        )
        result["hidden_now"] = [
            {key: row[key] for key in ("signal", "title", "missing", "note")}
            for row in facts.hidden_signals(registry, already)
        ]
    return result

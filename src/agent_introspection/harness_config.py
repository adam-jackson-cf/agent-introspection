"""Harness commands: list producer profiles, switch on a harness's export, preview parity.

``harness list`` shows every bundled profile, whether this machine enables it, its
binary on ``PATH``, and the version that binary prints (doctor's bounded version
probe; ``--no-version`` runs no binary). ``harness configure`` writes a
registered harness's ``export`` settings (and, opted in, its ``prompt_export`` ones)
into the harness's own config files through ``config_writers``: only the profile's
managed keys change, each changed file is first backed up to
``<file>.bak-<UTC timestamp>``, and a second run changes nothing. A setting whose
current value already reaches the collector over the other OTLP transport is left
alone unless forced (``profiles.equivalent_transport``). It then installs the
harness's activity hooks (``hook_install``), which ``harness remove-hooks`` takes out
again. The output names only managed keys, so no other value in those files is ever
printed, and shows their values as ``doctor`` does, without URL credentials or
queries. ``harness impact`` previews
which dashboard signals enabling a producer would hide (``capabilities.impact``).
"""

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

# Value parts of a managed setting shown in the output; any other nested key (such as
# an exporter's headers) is named but never shown.
_SHOWN_PARTS = frozenset({"otlp-http", "otlp-grpc", "endpoint", "protocol"})
_NOT_SHOWN = "(not shown)"


@dataclass(frozen=True)
class ConfigureOptions:
    """How ``harness configure`` runs.

    ``roots`` are extra config directories: every one is configured, beside the
    profile's existing roots for a file configured in each root, or in place of the
    profile's paths for a file configured once.
    """

    dry_run: bool = False
    prompt_export: bool = False
    force: bool = False
    roots: tuple[str, ...] = ()


class SettingChange(TypedDict):
    """One managed key's change in a config file.

    ``action`` is ``add``, ``change``, ``unchanged``, or ``satisfied``; ``old`` and
    ``new`` are the value before and after (a part the profile does not manage shown as
    ``(not shown)``). ``transport`` says how a satisfied value reaches the collector;
    ``opt_in`` marks a prompt-export setting left as it is, and ``on`` whether it is set.
    """

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
    """The changes to one config file, and the values to write."""

    changes: list[SettingChange]
    updates: dict[str, SettingValue]


def harness_list(enabled: Sequence[str] | None, *, versions: bool = True) -> dict[str, Any]:
    """Return every bundled profile with its status, enablement, binary, and version.

    ``enabled`` is config ``[harnesses] enabled``; None enables every registry harness.
    ``versions`` runs each binary found on PATH with the profile's ``version_args``
    through doctor's bounded probe (``doctor_harness.installed_version``, at most
    ``doctor_harness.VERSION_TIMEOUT_SECONDS`` each); False runs no binary.
    """
    registry = facts.load_registry()
    harnesses = facts.enabled_harnesses(registry, enabled)
    return {
        "enabled": list(harnesses),
        "harnesses": [
            _listing(profile, harnesses, versions) for profile in profiles.load_profiles(registry)
        ],
    }


def listed_version(profile: Profile, binary_path: str | None, run: bool) -> dict[str, Any]:
    """Return the version ``binary_path`` prints and whether the profile verified it.

    ``installed_version`` and ``version_verified`` are None when the version is unknown:
    the binary is not on PATH (``binary_path`` None), or it was not run or printed no
    version, which ``version_note`` then says. ``run`` False runs nothing, and neither
    does a profile with no ``version_args``, so a harness never starts a session here.
    """
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
    """Return the files to configure, resolved through symlinks, without repeats.

    The files ``profiles.config_paths`` resolves, which ``doctor`` reads: a file
    configured once (``roots = "first"``) goes to the first path whose variables are
    set, created if absent, and a file configured in each root to every chosen or
    existing root. ``roots`` adds explicit config directories (see
    ``ConfigureOptions``).

    Raises
    ------
    ValueError
        When a ``roots`` entry is not a directory, or no path's variables are set.
    """
    extras = [_root_file(root, config_file) for root in roots]
    chosen = profiles.config_paths(config_file, os.environ, Path.home(), extras)
    if not chosen:
        raise ValueError(
            f"{config_file.id}: no path to configure; set a variable in "
            + ", ".join(config_file.paths)
        )
    return list(dict.fromkeys(path.resolve() for path in chosen))


def _shown(value: object) -> object:
    """Return a managed setting's value as safe to print, as ``doctor`` shows it.

    Any part the profile does not manage is hidden, and text is shown through
    ``doctor_harness.shown_text``: a URL without credentials, query, or fragment.
    """
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
    """Return one managed key's change and its intended value."""
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
        # Prompt export is opt-in: report its state, never change it.
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
    """Plan one file's managed keys and, unless a dry run, write the changed ones.

    ``notes`` says how the file's layout changes beyond the managed key lines, such as
    a TOML table rewritten whole (``config_writers.TableUpdate``).
    """
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
    entries = [*hooks.get("runtime", []), hooks.get("settings", {}), hooks.get("plugin", {})]
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


def _settings_file(profile: Profile, roots: Sequence[str]) -> Path | None:
    """Return the JSON settings file that holds Claude Code's hooks, if the profile has one."""
    for config_file in profile.config_files:
        if config_file.format == "json":
            return config_targets(config_file, roots)[0]
    return None


def remove_hooks(name: str, *, dry_run: bool = False) -> dict[str, Any]:
    """Take a registered harness's activity hooks out; its export settings stay."""
    profile = _registered(name)
    options = hook_install.HookOptions(home=Path.home(), dry_run=dry_run)
    hooks = hook_install.remove(profile.hook_adapter, _settings_file(profile, ()), options)
    return {
        "harness": profile.key,
        "dry_run": dry_run,
        "hooks": hooks,
        "next_steps": _next_steps(profile, _hooks_changed(hooks)),
    }


def configure(name: str, otlp_endpoint: str | None, options: ConfigureOptions) -> dict[str, Any]:
    """Write a registered harness's export settings into its config files.

    ``otlp_endpoint`` is ``AppConfig.collector_endpoint`` (None for the default): ``[signoz]
    otlp_endpoint``, or ``[machine] export_endpoint`` on an additional machine.

    Then it installs the harness's activity hooks, when its profile names a hook adapter.

    Raises
    ------
    ValueError
        When the profile is unknown or a candidate, a root is not a directory, a config
        file cannot be parsed or changed safely, or a hook cannot be installed.
    """
    profile = _registered(name)
    context = profiles.export_context(profile, otlp_endpoint)
    files = [
        _configure_file(profile, config_file, path, options, context)
        for config_file in profile.config_files
        for path in config_targets(config_file, options.roots)
    ]
    written = frozenset(Path(item["path"]).resolve() for item in files if item["written"])
    hooks = (
        hook_install.install(
            profile.hook_adapter,
            _settings_file(profile, options.roots),
            hook_install.HookOptions(home=Path.home(), dry_run=options.dry_run, backed_up=written),
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
    """Resolve a bundled profile by name, or load a draft profile file by path."""
    if name.endswith(".toml") or os.sep in name:
        path = Path(name).expanduser()
        if not path.is_file():
            raise ValueError(f"no profile file at {name}")
        return profiles.load_profile(path, registry)
    return profiles.resolve(name, profiles.load_profiles(registry))


def _projection(profile: Profile, registry: facts.Registry) -> dict[str, dict[str, int]]:
    """Count each projection outcome per surface (the profile key for a candidate)."""
    surfaces = profile.registry_keys or ("",)
    summary: dict[str, dict[str, int]] = {}
    for surface in surfaces:
        counts = Counter(capabilities.project(profile, registry, surface).values())
        summary[surface or profile.key] = {
            outcome: counts[outcome] for outcome in capabilities.OUTCOMES
        }
    return summary


def harness_impact(name: str, enabled: Sequence[str] | None) -> dict[str, Any]:
    """Preview signal parity on this machine if the producer were also enabled.

    ``name`` is a profile key, registry harness key, or service name, or the path of a
    draft profile file. ``enabled`` is config ``[harnesses] enabled``. A producer that
    is already enabled is noted, with the signals it keeps hidden now.
    """
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

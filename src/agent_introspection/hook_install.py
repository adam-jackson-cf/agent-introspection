"""Install and remove a harness's activity hooks, the ``harness configure`` hook step.

The hook runtime ships in this package (``hook_runtime/``): the shim every hook runs,
and omp's plugin package. Installing copies it to the managed directory under the home
(``doctor_harness.MANAGED_RUNTIME``), so no hook ever runs a repository path, then
registers the hooks with the harness:

- Claude Code: one owned entry per activity event in the ``hooks`` of the settings file
  ``harness configure`` writes, each running ``/bin/sh <shim> claude-code <event>``.
  Entries this tool did not write (another tool's hooks) are kept as they are.
- omp: ``omp plugin link <runtime>/omp``, which records the plugin in omp's plugin
  directory; nothing goes in ``~/.omp/agent``.

When a non-default config file was selected, its path goes in a pointer file beside
the shim (``doctor_harness.CONFIG_POINTER``), which the shim exports as
``AGENT_INTROSPECTION_CONFIG``; with the default file the pointer is removed. One home
holds one pointer, so the last ``harness configure`` sets it for every harness.

Every write backs up the file it replaces to ``<file>.bak-<UTC timestamp>`` and is
atomic; a second run changes nothing. A dry run reports the same actions and writes
nothing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Final

from agent_introspection import config_writers
from agent_introspection.doctor_harness import (
    CLAUDE_HOOK_EVENTS,
    CONFIG_POINTER,
    MANAGED_RUNTIME,
    OMP_PLUGIN_LINK,
    SHIM,
    claude_hook_command,
    pointed_config,
)

# The runtime files, by path under ``hook_runtime/`` and the managed directory, and mode.
RUNTIME_FILES: Final = (
    (SHIM, 0o755),
    ("omp/activity.ts", 0o644),
    ("omp/package.json", 0o644),
)
OMP_PLUGIN: Final = "agent-introspection-activity"
TIMEOUT_SECONDS: Final = 10
_OMP_TIMEOUT_SECONDS: Final = 60

type CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


class HookInstallError(ValueError):
    """A hook cannot be installed or removed safely."""


@dataclass(frozen=True)
class HookOptions:
    """Where and how the hooks are installed.

    ``home`` holds the managed runtime and omp's plugin directory; ``run`` runs the
    ``omp`` command (a test passes a fake). ``backed_up`` names files this run already
    backed up or created, so the hook write adds no second backup of them.
    ``config_path`` is the selected config file, None for the default.
    """

    home: Path
    dry_run: bool = False
    run: CommandRunner | None = None
    backed_up: frozenset[Path] = frozenset()
    config_path: Path | None = None


def packaged(name: str) -> str:
    """Return the text of one runtime file as this package ships it."""
    return files("agent_introspection").joinpath("hook_runtime", name).read_text("utf-8")


def _install_file(name: str, mode: int, options: HookOptions) -> dict[str, Any]:
    target = options.home / MANAGED_RUNTIME / name
    content = packaged(name)
    current = target.read_text("utf-8") if target.is_file() else None
    stale_mode = target.is_file() and target.stat().st_mode & 0o777 != mode
    if current == content and not stale_mode:
        return {"path": str(target), "action": "unchanged", "backup": None}
    action = "add" if current is None else "change"
    backup: Path | None = None
    if not options.dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        backup = config_writers.backup(target) if current is not None else None
        config_writers.atomic_write(target, content, mode)
    return {"path": str(target), "action": action, "backup": str(backup) if backup else None}


def _point_config(options: HookOptions) -> dict[str, Any]:
    """Write the selected config path beside the shim, or remove it for the default."""
    target = options.home / MANAGED_RUNTIME / CONFIG_POINTER
    selected = pointed_config(options.config_path)
    content = f"{selected}\n" if selected is not None else None
    current = target.read_text("utf-8") if target.is_file() else None
    if current == content:
        return {"path": str(target), "action": "unchanged", "backup": None}
    action = "remove" if content is None else "add" if current is None else "change"
    if not options.dry_run:
        if content is None:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            config_writers.atomic_write(target, content, 0o644)
    return {"path": str(target), "action": action, "backup": None}


def runtime_files(adapter: str) -> tuple[tuple[str, int], ...]:
    """Return the runtime files a hook adapter needs: the shim, and for omp its plugin."""
    return tuple(item for item in RUNTIME_FILES if adapter == "omp" or "/" not in item[0])


def install_runtime(options: HookOptions, adapter: str) -> list[dict[str, Any]]:
    """Copy the shim (and for omp its plugin package) in, and point the shim at the config."""
    return [
        *(_install_file(name, mode, options) for name, mode in runtime_files(adapter)),
        _point_config(options),
    ]


# --- Claude Code ---------------------------------------------------------------------


def _owned_entry(shim: Path, event: str) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "hooks": [
            {
                "type": "command",
                "command": claude_hook_command(shim, event),
                "timeout": TIMEOUT_SECONDS,
            }
        ]
    }
    matcher = CLAUDE_HOOK_EVENTS[event]
    return {"matcher": matcher, **entry} if matcher is not None else entry


def is_owned(entry: object) -> bool:
    """Return whether a Claude Code hook entry runs this tool's shim (from any location)."""
    if not isinstance(entry, Mapping) or not isinstance(entry.get("hooks"), list):
        return False
    commands = [hook.get("command") for hook in entry["hooks"] if isinstance(hook, Mapping)]
    return bool(commands) and all(
        isinstance(text, str) and f"/{SHIM}" in text and " claude-code " in text
        for text in commands
    )


def _hook_table(settings: dict[str, Any]) -> dict[str, Any]:
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise HookInstallError("settings.hooks must be an object")
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            raise HookInstallError(f"settings.hooks.{event} must be a list")
    return hooks


def without_claude_hooks(settings: dict[str, Any]) -> dict[str, Any]:
    """Return settings without any entry that runs this tool's shim."""
    hooks = _hook_table(settings)
    for event in list(hooks):
        kept = [entry for entry in hooks[event] if not is_owned(entry)]
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if not hooks:
        del settings["hooks"]
    return settings


def with_claude_hooks(settings: dict[str, Any], shim: Path) -> dict[str, Any]:
    """Return settings with exactly one owned entry per activity event, appended last."""
    hooks = _hook_table(without_claude_hooks(settings))
    for event in CLAUDE_HOOK_EVENTS:
        hooks.setdefault(event, []).append(_owned_entry(shim, event))
    return settings


def _load_settings(path: Path) -> tuple[str | None, dict[str, Any]]:
    if not path.exists():
        return None, {}
    text = path.read_text("utf-8")
    try:
        settings = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        raise HookInstallError(f"{path} is not valid JSON") from None
    if not isinstance(settings, dict):
        raise HookInstallError(f"{path} must hold a JSON object")
    return text, settings


def write_claude_hooks(path: Path, options: HookOptions, *, remove: bool = False) -> dict[str, Any]:
    """Add (or with ``remove``, take out) the owned activity hooks in one settings file.

    ``path`` is followed through a symlink, so a linked settings file is written in place.
    """
    path = path.resolve()
    before, settings = _load_settings(path)
    shim = options.home / MANAGED_RUNTIME / SHIM
    updated = without_claude_hooks(settings) if remove else with_claude_hooks(settings, shim)
    after = config_writers.render_json(updated, before or "") if updated or before else ""
    events = [] if remove else list(CLAUDE_HOOK_EVENTS)
    if after == (before or ""):
        return {"path": str(path), "action": "unchanged", "backup": None, "events": events}
    backup: Path | None = None
    if not options.dry_run:
        mode = path.stat().st_mode & 0o777 if before is not None else config_writers.NEW_FILE_MODE
        fresh = before is not None and path not in options.backed_up
        backup = config_writers.backup(path) if fresh else None
        config_writers.atomic_write(path, after, mode)
    action = "remove" if remove else ("add" if before is None else "change")
    return {
        "path": str(path),
        "action": action,
        "backup": str(backup) if backup else None,
        "events": events,
    }


# --- omp -----------------------------------------------------------------------------


def _run_omp(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, capture_output=True, text=True, timeout=_OMP_TIMEOUT_SECONDS, check=False
    )


def _which(name: str) -> str | None:
    return shutil.which(name)


def require(adapter: str) -> str:
    """Refuse an adapter whose binary is missing, before anything is written; return it."""
    if adapter != "omp":
        return ""
    binary = _which("omp")
    if binary is None:
        raise HookInstallError("omp is not on PATH: install omp before its activity hooks")
    return binary


def _omp(arguments: Sequence[str], options: HookOptions) -> None:
    binary = require("omp")
    try:
        result = (options.run or _run_omp)([binary, *arguments])
    except subprocess.SubprocessError as exc:
        raise HookInstallError(f"`omp {' '.join(arguments)}` failed: {exc}") from None
    if result.returncode != 0:
        raise HookInstallError(f"`omp {' '.join(arguments)}` exited {result.returncode}")


def link_omp_plugin(options: HookOptions, *, remove: bool = False) -> dict[str, Any]:
    """Link the managed plugin into omp (or with ``remove``, uninstall it)."""
    managed = options.home / MANAGED_RUNTIME / "omp"
    link = options.home / OMP_PLUGIN_LINK
    linked = link.exists() and link.resolve() == managed.resolve()
    if remove:
        if not link.is_symlink() and not link.exists():
            return {"plugin": OMP_PLUGIN, "action": "unchanged"}
        if not options.dry_run:
            _omp(["plugin", "uninstall", OMP_PLUGIN], options)
        return {"plugin": OMP_PLUGIN, "action": "remove"}
    if linked:
        return {"plugin": OMP_PLUGIN, "action": "unchanged"}
    if not options.dry_run:
        _omp(["plugin", "link", str(managed)], options)
    return {"plugin": OMP_PLUGIN, "action": "link"}


def install(adapter: str, settings: Sequence[Path], options: HookOptions) -> dict[str, Any]:
    """Install the runtime and register one harness's hooks; ``adapter`` is its hook adapter.

    ``settings`` are the Claude Code settings files ``harness configure`` writes, one per
    root.
    """
    require(adapter)
    result: dict[str, Any] = {"runtime": install_runtime(options, adapter)}
    if adapter == "claude-code":
        if not settings:
            raise HookInstallError("no Claude Code settings file to hold the hooks")
        result["settings"] = [write_claude_hooks(path, options) for path in settings]
    elif adapter == "omp":
        result["plugin"] = link_omp_plugin(options)
    return result


def remove(adapter: str, settings: Sequence[Path], options: HookOptions) -> dict[str, Any]:
    """Unregister one harness's hooks; the shared runtime stays for the other harnesses."""
    if adapter == "claude-code" and settings:
        return {"settings": [write_claude_hooks(path, options, remove=True) for path in settings]}
    if adapter == "omp":
        return {"plugin": link_omp_plugin(options, remove=True)}
    return {}

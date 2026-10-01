#!/usr/bin/env python3
"""Install or remove the Claude Code activity hooks in ``~/.claude/settings.json``.

The six activity events run the managed ``activity-shim.sh``, which hands the hook
envelope to a detached ``agent-introspection hook claude-code <event>`` and exits 0
with no output. Every other hook (including third-party ones) is kept as it is.
A changed settings file is first copied to ``settings.json.bak-<UTC timestamp>``.

Usage: install-activity.py [--remove] [--dry-run] [--settings PATH] [--runtime-dir DIR]
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import shlex
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PRODUCER = "claude-code"
SHIM = "activity-shim.sh"
MANAGED_RUNTIME = Path(".local/lib/agent-introspection/activity-hooks-v1")
# Tool events take a tool-name matcher; the others take none.
EVENTS = {
    "PreToolUse": "*",
    "PostToolUseFailure": "*",
    "UserPromptSubmit": None,
    "Stop": None,
    "StopFailure": None,
    "SubagentStart": None,
}
TIMEOUT_SECONDS = 10


class InstallError(RuntimeError):
    """The settings file cannot be changed safely."""


def command(shim: Path, event: str) -> str:
    return f"/bin/sh {shlex.quote(str(shim))} {PRODUCER} {event}"


def owned_entry(shim: Path, event: str) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "hooks": [{"type": "command", "command": command(shim, event), "timeout": TIMEOUT_SECONDS}]
    }
    matcher = EVENTS[event]
    if matcher is not None:
        entry = {"matcher": matcher, **entry}
    return entry


def is_owned(entry: object) -> bool:
    """Return whether a hook entry is one this installer wrote (any shim location)."""
    if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
        return False
    commands = [hook.get("command") for hook in entry["hooks"] if isinstance(hook, dict)]
    return bool(commands) and all(
        isinstance(text, str) and f"/{SHIM}" in text and f" {PRODUCER} " in text
        for text in commands
    )


def _hooks(settings: dict[str, Any]) -> dict[str, Any]:
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise InstallError("settings.hooks must be an object")
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            raise InstallError(f"settings.hooks.{event} must be a list")
    return hooks


def remove(settings: dict[str, Any]) -> dict[str, Any]:
    """Return settings without any installer-owned activity hook entries."""
    hooks = _hooks(settings)
    for event in list(hooks):
        kept = [entry for entry in hooks[event] if not is_owned(entry)]
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if not hooks:
        del settings["hooks"]
    return settings


def install(settings: dict[str, Any], shim: Path) -> dict[str, Any]:
    """Return settings with exactly one owned entry per activity event, appended last."""
    settings = remove(settings)
    hooks = _hooks(settings)
    for event in EVENTS:
        hooks.setdefault(event, []).append(owned_entry(shim, event))
    return settings


def render(settings: dict[str, Any]) -> str:
    return json.dumps(settings, indent=2, ensure_ascii=False) + "\n"


def load(path: Path) -> tuple[str, dict[str, Any]]:
    if not path.exists():
        return "", {}
    text = path.read_text(encoding="utf-8")
    try:
        settings = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError as error:
        raise InstallError(f"{path} is not valid JSON") from error
    if not isinstance(settings, dict):
        raise InstallError(f"{path} must contain a JSON object")
    return text, settings


def atomic_write(path: Path, content: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def backup(path: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = path.with_name(f"{path.name}.bak-{stamp}")
    shutil.copy2(path, target)
    return target


def install_shim(runtime_dir: Path) -> Path:
    source = Path(__file__).resolve().parents[2] / SHIM
    target = runtime_dir / SHIM
    content = source.read_text(encoding="utf-8")
    if target.exists() and target.read_text(encoding="utf-8") != content:
        print(f"backup: {backup(target)}")
    atomic_write(target, content, 0o755)
    return target


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--remove", action="store_true", help="remove the activity hooks")
    parser.add_argument("--dry-run", action="store_true", help="print the diff, change nothing")
    parser.add_argument("--settings", type=Path, default=Path.home() / ".claude/settings.json")
    parser.add_argument("--runtime-dir", type=Path, default=Path.home() / MANAGED_RUNTIME)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    args.settings = args.settings.resolve()  # write through a symlinked settings file
    before, settings = load(args.settings)
    shim = args.runtime_dir / SHIM
    updated = remove(settings) if args.remove else install(settings, shim)
    after = render(updated) if updated or before else ""
    if args.dry_run:
        sys.stdout.writelines(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                str(args.settings),
                f"{args.settings} (proposed)",
            )
        )
        return 0
    if not args.remove:
        install_shim(args.runtime_dir)
    if after == before:
        print(f"{args.settings}: already up to date")
        return 0
    if args.settings.exists():
        print(f"backup: {backup(args.settings)}")
    mode = args.settings.stat().st_mode & 0o777 if args.settings.exists() else 0o600
    atomic_write(args.settings, after, mode)
    action = "removed from" if args.remove else "installed in"
    print(f"activity hooks {action} {args.settings}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (InstallError, OSError) as error:
        print(f"install-activity: {error}", file=sys.stderr)
        raise SystemExit(os.EX_CONFIG) from error

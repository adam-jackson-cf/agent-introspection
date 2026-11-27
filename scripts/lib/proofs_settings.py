from __future__ import annotations

import re
import subprocess
import tomllib
from collections.abc import Iterable
from functools import cache
from pathlib import Path
from typing import Any

from lib import filesystem

PROOFS_FILE = "proofs.toml"
GIT_TIMEOUT_SECONDS = 30


class SettingsError(ValueError):
    pass


def load_proofs(repo: Path) -> dict[str, Any]:
    path = repo / PROOFS_FILE
    try:
        return filesystem.read_toml(path)
    except OSError as error:
        raise SettingsError(f"{PROOFS_FILE} is unreadable: {error}") from error
    except tomllib.TOMLDecodeError as error:
        raise SettingsError(f"{PROOFS_FILE} does not parse: {error}") from error


def table(document: dict[str, Any], name: str) -> dict[str, Any]:
    node: Any = document
    for part in name.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    if not isinstance(node, dict):
        raise SettingsError(f"{PROOFS_FILE} has no [{name}] table")
    return node


def string(settings: dict[str, Any], key: str, owner: str) -> str:
    value = settings.get(key)
    if not isinstance(value, str) or not value:
        raise SettingsError(f"{PROOFS_FILE} [{owner}] {key} must be a nonempty string")
    return value


def strings(settings: dict[str, Any], key: str, owner: str) -> list[str]:
    value = settings.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise SettingsError(f"{PROOFS_FILE} [{owner}] {key} must be a list of nonempty strings")
    return list(value)


def exceptions(document: dict[str, Any], rule: str) -> list[dict[str, Any]]:
    entries = document.get("exception", [])
    if not isinstance(entries, list):
        raise SettingsError(f"{PROOFS_FILE} exception must be an array of tables")
    return [entry for entry in entries if isinstance(entry, dict) and entry.get("rule") == rule]


def approved_paths(document: dict[str, Any], rule: str) -> set[str]:
    approved: set[str] = set()
    for entry in exceptions(document, rule):
        if all(
            isinstance(entry.get(key), str) and entry[key]
            for key in ("path", "reason", "approved_by")
        ):
            approved.add(entry["path"])
    return approved


@cache
def _glob(pattern: str) -> re.Pattern[str]:
    parts: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            parts.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            parts.append(".*")
            index += 2
        else:
            character = pattern[index]
            parts.append({"*": "[^/]*", "?": "[^/]"}.get(character, re.escape(character)))
            index += 1
    return re.compile("".join(parts))


def matches(path: str, patterns: Iterable[str]) -> bool:
    return any(_glob(pattern).fullmatch(path) for pattern in patterns)


def base_ref(root: Path) -> str:
    return string(table(load_proofs(root), "scope"), "base_ref", "scope")


def _git_lines(root: Path, args: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, stdout=subprocess.PIPE, timeout=GIT_TIMEOUT_SECONDS
    )


def git_text(root: Path, args: list[str]) -> str:
    return _git_lines(root, args).stdout.decode("utf-8")


def event_base(root: Path, event: str) -> str:
    if event == "--staged":
        return "HEAD"
    if event == "--merge-base":
        return git_text(root, ["merge-base", "HEAD", base_ref(root)]).strip()
    raise SettingsError(f"unknown event {event}; use --staged or --merge-base")


def changed_paths(root: Path, event: str, filters: str) -> list[str]:
    base = event_base(root, event)
    scope = ["--cached"] if event == "--staged" else []
    output = git_text(
        root, ["diff", *scope, "--name-only", "-z", f"--diff-filter={filters}", "-M", base]
    )
    return sorted(name for name in output.split("\0") if name)


def base_text(root: Path, event: str, path: str) -> str | None:
    base = event_base(root, event)
    listed = git_text(root, ["ls-tree", "--name-only", base, "--", path]).strip()
    if not listed:
        return None
    return git_text(root, ["show", f"{base}:{path}"])


def under(path: str, directory: str) -> bool:
    return path == directory or path.startswith(directory.rstrip("/") + "/")


def _git_ls_files(root: Path, pathspecs: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *pathspecs],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        timeout=GIT_TIMEOUT_SECONDS,
    )


def tracked_files(root: Path, patterns: Iterable[str]) -> list[str]:
    pathspecs = [f":(glob){pattern}" for pattern in patterns]
    if not pathspecs:
        return []
    completed = _git_ls_files(root, pathspecs)
    names = sorted({raw.decode("utf-8") for raw in completed.stdout.split(b"\0") if raw})
    return [name for name in names if filesystem.is_file(root / name)]

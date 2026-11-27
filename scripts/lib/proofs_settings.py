from __future__ import annotations

import re
import subprocess
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

PROOFS_FILE = "proofs.toml"
GIT_TIMEOUT_SECONDS = 30


class SettingsError(ValueError):
    pass


def settings_table(root: Path, name: str) -> dict[str, Any]:
    path = root / PROOFS_FILE
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise SettingsError(f"{PROOFS_FILE}: cannot read [{name}]: {error}") from error
    table = data.get(name)
    if not isinstance(table, dict):
        raise SettingsError(f"{PROOFS_FILE}: [{name}] is missing; declare it")
    return table


def string_list(table: dict[str, Any], key: str, section: str = "layout") -> list[str]:
    value = table.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SettingsError(f"{PROOFS_FILE}: [{section}] {key} must be a list of strings")
    return list(value)


def string_value(table: dict[str, Any], key: str, section: str = "layout") -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value:
        raise SettingsError(f"{PROOFS_FILE}: [{section}] {key} must be a non-empty string")
    return value


def exceptions(root: Path, rule: str) -> set[str]:
    path = root / PROOFS_FILE
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise SettingsError(f"{PROOFS_FILE}: cannot read [[exception]]: {error}") from error
    entries = data.get("exception", [])
    if not isinstance(entries, list):
        raise SettingsError(f"{PROOFS_FILE}: [[exception]] must be an array of tables")
    approved: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or entry.get("rule") != rule:
            continue
        missing = [key for key in ("path", "reason", "approved_by") if not entry.get(key)]
        if missing:
            raise SettingsError(f"{PROOFS_FILE}: exception[{index}] for {rule} lacks {missing}")
        approved.add(str(entry["path"]))
    return approved


def base_ref(root: Path) -> str:
    return string_value(settings_table(root, "structure"), "base_ref", "structure")


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


def glob_regex(pattern: str) -> re.Pattern[str]:
    parts: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            parts.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            parts.append(".*")
            index += 2
        elif pattern[index] == "*":
            parts.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            parts.append("[^/]")
            index += 1
        else:
            parts.append(re.escape(pattern[index]))
            index += 1
    return re.compile("".join(parts) + r"\Z")


def matches_any(path: str, patterns: Iterable[str]) -> bool:
    return any(glob_regex(pattern).match(path) for pattern in patterns)


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
    return [name for name in names if (root / name).is_file()]

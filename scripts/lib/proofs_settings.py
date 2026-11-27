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

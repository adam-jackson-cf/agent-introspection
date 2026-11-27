from __future__ import annotations

import re
import tomllib
from functools import cache
from pathlib import Path
from typing import Any

PROOFS_FILE = "proofs.toml"


class SettingsError(ValueError):
    pass


def load_proofs(repo: Path) -> dict[str, Any]:
    path = repo / PROOFS_FILE
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
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
        if all(isinstance(entry.get(key), str) and entry[key] for key in ("path", "reason", "approved_by")):
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


def matches(path: str, patterns: list[str]) -> bool:
    return any(_glob(pattern).fullmatch(path) for pattern in patterns)

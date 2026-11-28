from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from lib import filesystem
from lib.structure.finding import Finding
from lib.structure.path_rules import name_stem
from lib.structure.repo_git import Change
from lib.structure.scope import Scope

RESIDUE = "RENAME.residue"


def _names(path: str) -> list[str]:
    parts = path.split("/")
    return [*parts[:-1], name_stem(parts[-1])]


def retired_names(change: Change) -> list[tuple[str, str]]:
    if change.old_path is None:
        return []
    old, new = _names(change.old_path), _names(change.path)
    current = set(new)
    return [
        (name, new[index] if len(new) == len(old) else new[-1])
        for index, name in enumerate(old)
        if name not in current
    ]


def _alive(head_paths: tuple[str, ...]) -> set[str]:
    return {name for path in head_paths for name in _names(path)}


def _pattern(name: str) -> re.Pattern[str]:
    variants = sorted({name, name.replace("_", "-"), name.replace("-", "_")})
    alternatives = "|".join(re.escape(variant) for variant in variants)
    return re.compile(rf"(?<![A-Za-z0-9_-])(?:{alternatives})(?![A-Za-z0-9_-])")


def _text(root: Path, path: str) -> str | None:
    target = root / path
    if filesystem.is_symlink(target) or not filesystem.is_file(target):
        return None
    data = filesystem.read_bytes(target)
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _line_findings(
    path: str, text: str, retired: list[tuple[str, str, Change, re.Pattern[str]]]
) -> Iterator[Finding]:
    for number, line in enumerate(text.splitlines(), start=1):
        for name, replacement, change, pattern in retired:
            if pattern.search(line):
                yield Finding(
                    path,
                    number,
                    RESIDUE,
                    "retired-name",
                    f"retired name '{name}' remains after {change.old_path} became {change.path}",
                    f"replace it with '{replacement}' in the same change, with no old-name alias",
                )


def residue_findings(root: Path, scope: Scope) -> set[Finding]:
    alive = _alive(scope.head_paths)
    retired = [
        (name, replacement, change, _pattern(name))
        for change in scope.renames
        for name, replacement in retired_names(change)
        if name not in alive
    ]
    if not retired:
        return set()
    findings: set[Finding] = set()
    for path in scope.head_paths:
        text = _text(root, path)
        if text is not None:
            findings.update(_line_findings(path, text, retired))
    return findings

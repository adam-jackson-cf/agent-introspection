#!/usr/bin/env python3
"""Share maintained-file discovery and pyproject loading between Python tool guardrails."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

from lib.python_quality_scope import candidate_python_relpaths, is_maintained_python_path

_SKIPPED_DIRECTORIES = frozenset({
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "node_modules",
    "venv",
})


def load_pyproject(path: Path) -> dict[str, Any]:
    """Return the parsed TOML document at ``path``."""
    with path.open("rb") as handle:
        return tomllib.load(handle)


def as_list(value: object) -> list[str]:
    """Normalise a TOML string-or-list setting to a list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    raise TypeError(f"expected a string or list, got {type(value).__name__}")


def _walk(root: Path) -> list[str]:
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if _SKIPPED_DIRECTORIES.isdisjoint(relative.parts):
            found.append(relative.as_posix())
    return found


def maintained_python_files(root: Path, exclude_patterns: list[str] | None = None) -> list[str]:
    """Return maintained Python files, including untracked ones, minus tool exclude patterns.

    Untracked files are included so a new package cannot dodge coverage until it is committed.
    A directory that is not a Git checkout (canary fixtures) is walked instead.
    """
    candidates = candidate_python_relpaths(root) if (root / ".git").exists() else _walk(root)
    excludes = [re.compile(pattern) for pattern in exclude_patterns or []]
    return [
        path
        for path in candidates
        if is_maintained_python_path(path) and not any(rx.search(path) for rx in excludes)
    ]

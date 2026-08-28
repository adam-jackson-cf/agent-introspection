#!/usr/bin/env python3
"""Define the maintained Python files covered by project quality gates."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_EXCLUDED_PREFIXES = (
    ".git/",
    ".mypy_cache/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".venv/",
    "build/",
    "dist/",
    "node_modules/",
    "venv/",
)
_MAINTAINED_PREFIXES = ("scripts/", "src/", "tests/")


def is_excluded_python_path(path: str) -> bool:
    """Return whether a tracked Python path is generated or tool-owned."""
    normalized = path.removeprefix("./")
    return normalized.startswith(_EXCLUDED_PREFIXES) or "/__pycache__/" in f"/{normalized}/"


def is_maintained_python_path(path: str) -> bool:
    """Return whether a tracked Python path belongs to this project's source surface."""
    normalized = path.removeprefix("./")
    if not normalized.endswith(".py") or is_excluded_python_path(normalized):
        return False
    return normalized.startswith(_MAINTAINED_PREFIXES) or (
        normalized.startswith(".agents/skills/") and "/scripts/" in normalized
    )


def tracked_python_files() -> list[str]:
    """Return every present tracked Python file or reject an unclassified file."""
    completed = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.py"],
        check=True,
        stdout=subprocess.PIPE,
    )
    paths = sorted(
        raw_path.decode("utf-8") for raw_path in completed.stdout.split(b"\0") if raw_path
    )
    paths = [path for path in paths if Path(path).is_file()]
    unclassified = [
        path
        for path in paths
        if not is_excluded_python_path(path) and not is_maintained_python_path(path)
    ]
    if unclassified:
        formatted_paths = "\n".join(f"  - {path}" for path in unclassified)
        raise RuntimeError(
            "Python quality scope has unclassified tracked files; classify each path in "
            f"scripts/python_quality_scope.py:\n{formatted_paths}"
        )
    return [path for path in paths if is_maintained_python_path(path)]


def main() -> int:
    """Write the NUL-delimited maintained Python file set for shell callers."""
    try:
        sys.stdout.buffer.write(b"\0".join(path.encode("utf-8") for path in tracked_python_files()))
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

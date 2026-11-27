#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
from pathlib import Path

from lib.python_tool_scope import as_list, load_pyproject, maintained_python_files

_DESCRIPTION = """Type-check every maintained Python file and fail when the mypy target list drifts.

The drift check enforces ``MYPY.package-coverage``: every maintained Python file (including
untracked ones) must be covered by ``[tool.mypy] files``/``packages``, and every listed entry must
exist. Skill scripts under ``.agents/skills/*/scripts`` share module basenames and contain
non-importable names, so they are checked one file per mypy invocation with the same config.
"""

_ISOLATED_GLOB = ".agents/skills/*/scripts/*"


def _is_isolated(path: str) -> bool:
    return fnmatch.fnmatchcase(path, _ISOLATED_GLOB)


def _entries(settings: dict[str, object], root: Path) -> list[str]:
    bases = [Path(item) for item in as_list(settings.get("mypy_path")) or ["."]]
    entries: list[str] = []
    for item in as_list(settings.get("files")):
        if any(char in item for char in "*?["):
            matches = sorted(
                match.relative_to(root).as_posix() for match in root.glob(item.removeprefix("./"))
            )
            entries.extend(matches or [item])
        else:
            entries.append(_normalise_entry(item))
    for package in as_list(settings.get("packages")):
        package_path = Path(*package.split("."))
        matches = [
            _normalise_entry((base / package_path).as_posix())
            for base in bases
            if (root / base / package_path).exists()
        ]
        entries.extend(matches or [f"<package:{package}>"])
    return entries


def _normalise_entry(item: str) -> str:
    return Path(item).as_posix()


def _covered(path: str, entries: list[str]) -> bool:
    return any(entry == "." or path == entry or path.startswith(f"{entry}/") for entry in entries)


def coverage_problems(root: Path, config: Path) -> list[str]:
    settings = load_pyproject(config).get("tool", {}).get("mypy", {})
    entries = _entries(settings, root)
    if not entries:
        return [
            f"{config.name}: MYPY.package-coverage: [tool.mypy] lists no files or packages; "
            "list every maintained source root in [tool.mypy] files"
        ]
    problems = [
        f"{config.name}: MYPY.package-coverage: configured entry does not exist: {entry}; "
        "delete the entry or restore its path"
        for entry in entries
        if entry.startswith("<package:") or not (root / entry).exists()
    ]
    excludes = [re.compile(pattern) for pattern in as_list(settings.get("exclude"))]
    maintained = maintained_python_files(root)
    problems.extend(
        f"{config.name}: MYPY.SETTING.exclude: [tool.mypy] exclude {pattern.pattern!r} hides "
        f"maintained file {path}; narrow the pattern to non-maintained paths"
        for pattern in excludes
        for path in maintained
        if pattern.search(path)
    )
    problems.extend(
        f"{path}: MYPY.package-coverage: not covered by [tool.mypy] files/packages; "
        "add its source root to [tool.mypy] files"
        for path in maintained
        if not _is_isolated(path) and not _covered(path, entries)
    )
    return problems


def _mypy(config: Path, root: Path, targets: list[str]) -> int:
    command = [
        sys.executable,
        "-m",
        "mypy",
        "--config-file",
        str(config),
        "--cache-dir",
        os.devnull,
        *targets,
    ]
    return subprocess.run(command, cwd=root, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=_DESCRIPTION)
    parser.add_argument("--config", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--drift-only", action="store_true", help="skip running mypy")
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args(argv)
    config = args.config.resolve()
    root = config.parent
    if args.paths:
        return _mypy(config, root, [path.resolve().as_posix() for path in args.paths])
    problems = coverage_problems(root, config)
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems or args.drift_only:
        return 1 if problems else 0
    settings = load_pyproject(config).get("tool", {}).get("mypy", {})
    isolated = [
        path
        for path in maintained_python_files(root, as_list(settings.get("exclude")))
        if _is_isolated(path)
    ]
    status = _mypy(config, root, [])
    for path in isolated:
        status = max(status, _mypy(config, root, [path]))
    return status


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Type-check every maintained Python file and fail when the mypy target list drifts.

The drift check enforces ``MYPY.package-coverage``: every maintained Python file (including
untracked ones) must be covered by ``[tool.mypy] files``/``packages``, and every listed entry must
exist. Skill scripts under ``.agents/skills/*/scripts`` share module basenames and contain
non-importable names, so they are checked one file per mypy invocation with the same config.
"""

from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
from pathlib import Path

from lib.python_tool_scope import as_list, load_pyproject, maintained_python_files

_ISOLATED_GLOB = ".agents/skills/*/scripts/*"


def _is_isolated(path: str) -> bool:
    return fnmatch.fnmatchcase(path, _ISOLATED_GLOB)


def _entries(settings: dict[str, object], root: Path) -> list[str]:
    """Return configured source roots as root-relative POSIX paths, with globs expanded."""
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
    """Return ``item`` as a root-relative POSIX path; ``.`` stays ``.``."""
    return Path(item).as_posix()


def _covered(path: str, entries: list[str]) -> bool:
    return any(entry == "." or path == entry or path.startswith(f"{entry}/") for entry in entries)


def coverage_problems(root: Path, config: Path) -> list[str]:
    """Return one diagnostic per uncovered maintained file or stale configured entry."""
    settings = load_pyproject(config).get("tool", {}).get("mypy", {})
    entries = _entries(settings, root)
    if not entries:
        return ["MYPY.package-coverage: [tool.mypy] lists no files or packages"]
    problems = [
        f"MYPY.package-coverage: configured entry does not exist: {entry}"
        for entry in entries
        if entry.startswith("<package:") or not (root / entry).exists()
    ]
    excludes = as_list(settings.get("exclude"))
    problems.extend(
        f"MYPY.package-coverage: {path} is not covered by [tool.mypy] files/packages"
        for path in maintained_python_files(root, excludes)
        if not _is_isolated(path) and not _covered(path, entries)
    )
    return problems


def _mypy(config: Path, root: Path, targets: list[str]) -> int:
    command = [sys.executable, "-m", "mypy", "--config-file", str(config), *targets]
    return subprocess.run(command, cwd=root, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    """Run the drift check, then mypy for the configured targets and isolated skill scripts."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--drift-only", action="store_true", help="skip running mypy")
    args = parser.parse_args(argv)
    config = args.config.resolve()
    root = config.parent
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

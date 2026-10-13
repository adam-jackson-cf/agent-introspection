#!/usr/bin/env python3
"""Fail when Ruff/mypy suppressions grow beyond the committed baseline.

Detects new ``# noqa``, ``# ruff: noqa``, ``# type: ignore`` and ``# mypy: ignore-errors``
comments in maintained Python files, and new entries in the Ruff/mypy configuration that
silence findings (``ignore``, ``per-file-ignores``, ``exclude``, mypy overrides and
error-disabling settings). Removing a suppression is always allowed; adding one requires a
reviewed ``--update`` of the baseline file, which makes the growth visible in review.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import tokenize
from collections import Counter
from pathlib import Path
from typing import Any

from lib.python_tool_scope import load_pyproject, maintained_python_files

_DEFAULT_BASELINE = Path("scripts/python-suppression-baseline.json")
_DIRECTIVE = re.compile(
    r"#\s*(?P<text>(?:ruff:\s*noqa|noqa|type:\s*ignore|mypy:\s*ignore-errors)\b[^#]*)",
    re.IGNORECASE,
)
_RUFF_LIST_SETTINGS = ("ignore", "extend-ignore", "unfixable")
_RUFF_MAP_SETTINGS = ("per-file-ignores", "extend-per-file-ignores")
_MYPY_SILENCERS = ("disable_error_code", "ignore_errors", "ignore_missing_imports")


def comment_suppressions(source: str) -> Counter[str]:
    """Count normalised suppression directives found in real comments of ``source``."""
    found: Counter[str] = Counter()
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return found
    for token in tokens:
        if token.type != tokenize.COMMENT:
            continue
        for match in _DIRECTIVE.finditer(token.string):
            found[" ".join(match.group("text").lower().split())] += 1
    return found


def _map_setting_entries(key: str, mapping: dict[str, list[str]]) -> set[str]:
    """Return one entry per code that a Ruff pattern-to-codes setting silences."""
    return {
        f"ruff.lint.{key}:{pattern}:{code}" for pattern, codes in mapping.items() for code in codes
    }


def config_suppressions(document: dict[str, Any]) -> set[str]:
    """Return one entry per configured suppression in the Ruff and mypy settings."""
    tool = document.get("tool", {})
    entries: set[str] = set()
    ruff = tool.get("ruff", {})
    lint = ruff.get("lint", {})
    for key in ("exclude", "extend-exclude"):
        entries.update(f"ruff.{key}:{item}" for item in ruff.get(key, []))
    for key in _RUFF_LIST_SETTINGS:
        entries.update(f"ruff.lint.{key}:{item}" for item in lint.get(key, []))
    for key in _RUFF_MAP_SETTINGS:
        entries.update(_map_setting_entries(key, lint.get(key, {})))
    mypy = tool.get("mypy", {})
    for key in ("exclude", *_MYPY_SILENCERS):
        value = mypy.get(key)
        if value not in (None, False, []):
            entries.add(f"mypy.{key}={json.dumps(value, sort_keys=True)}")
    for override in mypy.get("overrides", []):
        modules = json.dumps(sorted(override.get("module", [])))
        for key, value in sorted(override.items()):
            if key != "module":
                entries.add(f"mypy.override:{modules}:{key}={json.dumps(value)}")
    return entries


def snapshot(root: Path, config: Path) -> dict[str, Any]:
    """Collect the current comment and configuration suppressions."""
    document = load_pyproject(config)
    exclude = document.get("tool", {}).get("mypy", {}).get("exclude", [])
    files = maintained_python_files(root, [exclude] if isinstance(exclude, str) else exclude)
    comments = {
        path: dict(counts)
        for path in files
        if (counts := comment_suppressions((root / path).read_text(encoding="utf-8")))
    }
    return {
        "schema_version": 1,
        "comments": comments,
        "config": sorted(config_suppressions(document)),
    }


def growth(baseline: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Return one diagnostic per suppression present now but not allowed by ``baseline``."""
    problems: list[str] = []
    allowed_comments: dict[str, dict[str, int]] = baseline.get("comments", {})
    for path, counts in sorted(current["comments"].items()):
        for text, count in sorted(counts.items()):
            if count > allowed_comments.get(path, {}).get(text, 0):
                problems.append(f"RUFF.IGNORES: new suppression comment in {path}: # {text}")
    problems.extend(
        f"RUFF.IGNORES: new suppression configuration entry: {entry}"
        for entry in current["config"]
        if entry not in set(baseline.get("config", []))
    )
    return problems


def _check_file(path: Path) -> list[str]:
    counts = comment_suppressions(path.read_text(encoding="utf-8"))
    return [
        f"RUFF.IGNORES: new suppression comment in {path.name}: # {text}" for text in sorted(counts)
    ]


def main(argv: list[str] | None = None) -> int:
    """Compare current suppressions with the baseline, or rewrite it with ``--update``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--baseline", type=Path, default=_DEFAULT_BASELINE)
    parser.add_argument("--update", action="store_true", help="rewrite the baseline (review it)")
    parser.add_argument("--check-file", type=Path, help="treat every suppression in FILE as new")
    args = parser.parse_args(argv)
    if args.check_file is not None:
        problems = _check_file(args.check_file)
    else:
        current = snapshot(args.config.resolve().parent, args.config.resolve())
        if args.update:
            args.baseline.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
            return 0
        problems = growth(json.loads(args.baseline.read_text(encoding="utf-8")), current)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())

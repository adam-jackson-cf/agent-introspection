#!/usr/bin/env python3
"""Fail when the owned coverage slice is below its minimum (TEST.coverage-minimum).

Reads a coverage.py JSON report (`coverage json` / `pytest --cov-report=json`) and the
owned slice in scripts/coverage-slice.json.
"""

from __future__ import annotations

import fnmatch
import json
import sys
from pathlib import Path

_SLICE_CONFIG = Path(__file__).with_name("coverage-slice.json")


def slice_percent(report: dict[str, object], include: list[str], exclude: list[str]) -> float:
    """Return covered-statement percent over report files matching the owned slice."""
    files = report.get("files")
    if not isinstance(files, dict):
        raise ValueError("coverage report has no 'files' mapping")
    covered = statements = 0
    for name, entry in files.items():
        if not any(fnmatch.fnmatch(name, pattern) for pattern in include):
            continue
        if any(fnmatch.fnmatch(name, pattern) for pattern in exclude):
            continue
        summary = entry["summary"]
        covered += summary["covered_lines"]
        statements += summary["num_statements"]
    if statements == 0:
        raise ValueError("owned coverage slice matched no measured statements")
    return 100.0 * covered / statements


def main(argv: list[str]) -> int:
    """Check the JSON coverage report named by argv[0] against the owned slice."""
    if len(argv) != 1:
        print("usage: check-coverage-slice.py COVERAGE_JSON", file=sys.stderr)
        return 2
    config = json.loads(_SLICE_CONFIG.read_text(encoding="utf-8"))
    try:
        report = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
        percent = slice_percent(report, config["include"], config["exclude"])
    except (OSError, ValueError, KeyError) as error:
        print(f"TEST.coverage-minimum unusable coverage report: {error}", file=sys.stderr)
        return 1
    minimum = config["minimum_percent"]
    print(f"owned slice coverage {percent:.2f}% (minimum {minimum}%)", file=sys.stderr)
    if percent < minimum:
        print(f"TEST.coverage-minimum owned slice {percent:.2f}% < {minimum}%", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

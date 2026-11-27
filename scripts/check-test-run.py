#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lib.test_evidence import EvidenceError, RunReport, read_listing, read_report

RULE = "TEST.failure-propagation"
NOT_RUN = frozenset({"skipped", "xfail"})
BROKEN = frozenset({"failed", "error", "timeout"})


def _report_problems(path: Path, report: RunReport) -> list[str]:
    if not report.cases:
        return [f"{path}: {RULE}: tier {report.tier} ({report.framework}) ran no tests; "
                "fix the tier selector or the runner so the tier runs its tests"]
    problems = [
        f"{case.id}: {RULE}: the run report records {case.outcome} but the runner passed; "
        "make the runner exit nonzero when a test fails, errors or times out"
        for case in report.cases
        if case.outcome in BROKEN
    ]
    if all(case.outcome in NOT_RUN for case in report.cases):
        problems.append(
            f"{path}: {RULE}: every test in tier {report.tier} ({report.framework}) was skipped; "
            "a required tier must run its tests"
        )
    return problems


def _listing_problems(listing_path: Path, reports: list[RunReport]) -> list[str]:
    try:
        listing = read_listing(listing_path)
    except EvidenceError as error:
        return [f"{listing_path}: {RULE}: {error}; collect the tier again with its selector"]
    reported = {
        case.id for report in reports if report.framework == listing.framework for case in report.cases
    }
    return [
        f"{identifier}: {RULE}: collected for tier {listing.tier} but missing from the run report; "
        "rerun the tier and keep every collected test in its report"
        for identifier in listing.ids
        if identifier not in reported
    ]


def check(report_paths: list[Path], listing_paths: list[Path]) -> list[str]:
    problems: list[str] = []
    reports: list[RunReport] = []
    for path in report_paths:
        try:
            report = read_report(path)
        except EvidenceError as error:
            problems.append(f"{path}: {RULE}: {error}; rerun the tier so it writes its report")
            continue
        reports.append(report)
        problems.extend(_report_problems(path, report))
    for listing_path in listing_paths:
        problems.extend(_listing_problems(listing_path, reports))
    return problems


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="check-test-run.py")
    parser.add_argument("--listing", type=Path, action="append", default=[])
    parser.add_argument("reports", type=Path, nargs="+")
    arguments = parser.parse_args(argv)
    problems = check(arguments.reports, arguments.listing)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

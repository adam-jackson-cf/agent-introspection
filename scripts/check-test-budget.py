#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.machine_probe import machine_id
from lib.proofs_settings import PROOFS_FILE, SettingsError, approved_paths, load_proofs
from lib.test_evidence import EvidenceError, RunReport, read_report

WALL_RULE = "TEST.budget.t1-time"
PER_TEST_RULE = "TEST.budget.per-test-time"
TIER = "t1"


@dataclass(frozen=True)
class Limits:
    wall_seconds: float
    per_test_seconds: float


def _positive(entry: dict[str, Any], key: str, machine: str) -> float:
    value = entry.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SettingsError(f"[[test_budget.machine]] {machine} {key} must be a number")
    if not math.isfinite(value) or value <= 0:
        raise SettingsError(f"[[test_budget.machine]] {machine} {key} must be positive and finite")
    return float(value)


def machine_limits(document: dict[str, Any], machine: str) -> Limits | None:
    budget = document.get("test_budget", {})
    entries = budget.get("machine", []) if isinstance(budget, dict) else None
    if not isinstance(entries, list):
        raise SettingsError("[[test_budget.machine]] must be an array of tables")
    for entry in entries:
        if isinstance(entry, dict) and entry.get("id") == machine:
            approval = entry.get("approval")
            if not isinstance(approval, str) or not approval:
                raise SettingsError(f"[[test_budget.machine]] {machine} has no approval")
            return Limits(
                _positive(entry, "wall_limit_s", machine),
                _positive(entry, "per_test_limit_s", machine),
            )
    return None


def _reports(paths: list[Path], since: float) -> tuple[list[RunReport], list[str]]:
    reports: list[RunReport] = []
    problems: list[str] = []
    for path in paths:
        try:
            report = read_report(path)
        except EvidenceError as error:
            problems.append(f"{path}: {WALL_RULE}: {error}; rerun T1 so it writes a fresh report")
            continue
        if report.tier != TIER:
            problems.append(
                f"{path}: {WALL_RULE}: report is for tier {report.tier}, not {TIER}; "
                "pass only T1 run reports"
            )
        elif report.started_at < since:
            problems.append(
                f"{path}: {WALL_RULE}: report predates this run; rerun T1 for a fresh report"
            )
        else:
            reports.append(report)
    return reports, problems


def judge(reports: list[RunReport], limits: Limits, machine: str, deferred: set[str]) -> list[str]:
    wall = sum(report.wall_seconds for report in reports)
    problems: list[str] = []
    if wall > limits.wall_seconds:
        problems.append(
            f"{PROOFS_FILE}: {WALL_RULE}: T1 took {wall:.2f}s on {machine}, over the approved "
            f"{limits.wall_seconds:g}s; speed up the slowest T1 tests "
            "(a limit raise needs owner approval)"
        )
    problems.extend(
        f"{case.id}: {PER_TEST_RULE}: took {case.seconds:.3f}s on {machine}, over the approved "
        f"{limits.per_test_seconds:g}s; speed the test up, never move it to another tier"
        for report in reports
        for case in report.cases
        if case.seconds > limits.per_test_seconds and case.id not in deferred
    )
    return problems


def check(repo: Path, paths: list[Path], since: float) -> tuple[list[str], str]:
    machine = machine_id()
    reports, problems = _reports(paths, since)
    try:
        document = load_proofs(repo)
        limits = machine_limits(document, machine)
    except SettingsError as error:
        return [
            *problems,
            f"{PROOFS_FILE}: {WALL_RULE}: {error}; fix [[test_budget.machine]]",
        ], machine
    wall = sum(report.wall_seconds for report in reports)
    summary = f"T1 wall {wall:.2f}s across {sum(len(r.cases) for r in reports)} tests on {machine}"
    if limits is None:
        prompt = (
            f"{summary}; no approved limits for this machine: record five baseline T1 runs "
            f'and add a [[test_budget.machine]] entry with id = "{machine}" for owner approval'
        )
        return problems, prompt
    deferred = approved_paths(document, PER_TEST_RULE)
    return [*problems, *judge(reports, limits, machine, deferred)], summary


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="check-test-budget.py")
    parser.add_argument("--repo", type=Path, default=Path())
    parser.add_argument("--print-machine-id", action="store_true")
    parser.add_argument("--since", type=float)
    parser.add_argument("reports", type=Path, nargs="*")
    arguments = parser.parse_args(argv)
    if arguments.print_machine_id:
        print(machine_id())
        return 0
    if arguments.since is None or not arguments.reports:
        parser.error("--since and at least one T1 report are required")
    problems, summary = check(arguments.repo, arguments.reports, arguments.since)
    print(f"check-test-budget: {summary}", file=sys.stderr)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

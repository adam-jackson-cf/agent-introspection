#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys

RULE = "TEST.tier-selectors"
COLLECT_TIMEOUT_SECONDS = 300
NO_TESTS_COLLECTED = 5


def _pytest(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=COLLECT_TIMEOUT_SECONDS,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def collect(selector: str | None, paths: list[str]) -> tuple[list[str], str | None]:
    arguments = ["--collect-only", "-q", "-p", "no:cacheprovider", *paths]
    if selector is not None:
        arguments += ["-m", selector]
    completed = _pytest(arguments)
    ids = [line for line in completed.stdout.splitlines() if "::" in line and " " not in line]
    if completed.returncode not in {0, NO_TESTS_COLLECTED}:
        return ids, completed.stdout[-2000:] + completed.stderr[-2000:]
    return ids, None


def parse_tiers(arguments: list[str]) -> tuple[dict[str, str], list[str]]:
    tiers: dict[str, str] = {}
    paths: list[str] = []
    index = 0
    while index < len(arguments):
        if arguments[index] == "--tier" and index + 1 < len(arguments):
            name, separator, selector = arguments[index + 1].partition("=")
            if not separator or not name or not selector:
                raise ValueError(f"--tier needs NAME=SELECTOR, got {arguments[index + 1]!r}")
            tiers[name] = selector
            index += 2
        else:
            paths.append(arguments[index])
            index += 1
    if len(tiers) < 2:
        raise ValueError("pass every tier as --tier NAME=SELECTOR (at least two)")
    return tiers, paths


def tier_problems(selected: dict[str, list[str]], everything: list[str]) -> list[str]:
    problems: list[str] = []
    owners: dict[str, list[str]] = {}
    for tier, ids in selected.items():
        if not ids:
            problems.append(
                f"{RULE}: tier {tier} selects no tests; tag tests for it or drop the tier"
            )
        for test_id in ids:
            owners.setdefault(test_id, []).append(tier)
    for test_id in everything:
        tiers = owners.get(test_id, [])
        file = test_id.split("::", 1)[0]
        if not tiers:
            problems.append(
                f"{file}: {RULE}: {test_id} is selected by no tier; tag it so exactly one "
                "selector matches"
            )
        elif len(tiers) > 1:
            problems.append(
                f"{file}: {RULE}: {test_id} is selected by {', '.join(tiers)}; make the "
                "selectors disjoint"
            )
    files: dict[str, set[str]] = {}
    for tier, ids in selected.items():
        for test_id in ids:
            files.setdefault(test_id.split("::", 1)[0], set()).add(tier)
    problems.extend(
        f"{file}: {RULE}: holds tests of tiers {', '.join(sorted(tiers))}; move the tests of "
        "one tier into their own file so each file has one tier"
        for file, tiers in sorted(files.items())
        if len(tiers) > 1
    )
    return problems


def main(argv: list[str]) -> int:
    try:
        tiers, paths = parse_tiers(argv)
    except ValueError as error:
        print(f"{RULE}: {error}", file=sys.stderr)
        return 2
    everything, failure = collect(None, paths)
    problems = [f"{RULE}: collection failed: {failure}"] if failure else []
    if not everything:
        problems.append(f"{RULE}: the test runner collected no tests; fix collection")
    selected: dict[str, list[str]] = {}
    for name, selector in tiers.items():
        selected[name], failure = collect(selector, paths)
        if failure:
            problems.append(f"{RULE}: tier {name} ({selector}) failed to collect: {failure}")
    problems.extend(tier_problems(selected, everything))
    for problem in problems:
        print(problem, file=sys.stderr)
    counts = ", ".join(f"{name} {len(ids)}" for name, ids in selected.items())
    print(
        f"check-test-tiers: {len(everything)} tests ({counts}), {len(problems)} violations",
        file=sys.stderr,
    )
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

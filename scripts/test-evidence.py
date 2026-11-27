#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lib import bun_adapter, pytest_adapter
from lib.test_evidence import EvidenceError, write_listing, write_report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="test-evidence.py")
    commands = parser.add_subparsers(dest="command", required=True)
    normalise = commands.add_parser("normalise")
    normalise.add_argument("framework", choices=("pytest", "bun"))
    normalise.add_argument("--tier", required=True)
    normalise.add_argument("--prefix", default="")
    normalise.add_argument("junit", type=Path)
    normalise.add_argument("output", type=Path)
    collect = commands.add_parser("collect")
    collect.add_argument("--tier", required=True)
    collect.add_argument("--out", type=Path, required=True)
    collect.add_argument("--timeout-seconds", type=float, required=True)
    collect.add_argument("selection", nargs=argparse.REMAINDER)
    return parser


def _normalise(arguments: argparse.Namespace) -> None:
    if arguments.framework == "pytest":
        report = pytest_adapter.report(arguments.junit, arguments.tier)
    else:
        report = bun_adapter.report(arguments.junit, arguments.tier, arguments.prefix)
    write_report(report, arguments.output)


def _collect(arguments: argparse.Namespace) -> None:
    selection = [item for item in arguments.selection if item != "--"]
    listing = pytest_adapter.collect(Path.cwd(), arguments.tier, selection, arguments.timeout_seconds)
    write_listing(listing, arguments.out)
    print(f"test-evidence: tier {listing.tier} collected {len(listing.ids)} tests", file=sys.stderr)


def main(argv: list[str]) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "normalise":
            _normalise(arguments)
        else:
            _collect(arguments)
    except EvidenceError as error:
        print(f"test-evidence: TEST.failure-propagation: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

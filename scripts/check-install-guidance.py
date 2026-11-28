#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import shlex
import sys
from collections.abc import Sequence
from pathlib import Path

from lib import filesystem

_RULE = "PARITY.install-guidance"
_STEP = re.compile(r"^\s*step\s+\S+\s+\S+\s+(?P<quote>['\"])(?P<command>.*)(?P=quote)\s*$")
_FENCE = re.compile(r"^```(?P<lang>\S*)\s*$")
_ACQUISITION = ("git", "cd")


def documented_commands(guide: Path, section: str) -> list[tuple[int, list[str]]]:
    lines = filesystem.read_text(guide).splitlines()
    heading = f"## {section}"
    if heading not in lines:
        raise ValueError(f"{guide}: {_RULE}: no '{heading}' section; restore the install section")
    commands: list[tuple[int, list[str]]] = []
    inside = False
    for number, line in enumerate(lines[lines.index(heading) + 1 :], lines.index(heading) + 2):
        if line.startswith("## "):
            break
        fence = _FENCE.match(line)
        if fence and not inside:
            inside = fence.group("lang") in {"bash", "sh"}
            continue
        if fence:
            if commands:
                break
            inside = False
            continue
        tokens = shlex.split(line, comments=True) if inside else []
        if tokens and tokens[0] not in _ACQUISITION:
            commands.append((number, tokens))
    if not commands:
        raise ValueError(f"{guide}: {_RULE}: '{heading}' has no shell commands; document them")
    return commands


def installer_commands(installer: Path) -> list[list[str]]:
    commands = []
    for line in filesystem.read_text(installer).splitlines():
        match = _STEP.match(line)
        if match:
            tokens = shlex.split(match.group("command"))
            commands.append([token for token in tokens if not token.startswith("$")])
    if not commands:
        raise ValueError(f"{installer}: {_RULE}: no step commands; run the install as steps")
    return commands


def problems(guide: Path, installer: Path, section: str) -> list[str]:
    replayed = installer_commands(installer)
    return [
        f"{guide}:{number}: {_RULE}: documented command '{shlex.join(tokens)}' is not run by "
        f"{installer}; add a step that runs it or correct the documented command"
        for number, tokens in documented_commands(guide, section)
        if tokens not in replayed
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail when install docs and the installer drift.")
    parser.add_argument("--guide", type=Path, required=True)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--section", required=True)
    args = parser.parse_args(argv)
    try:
        found = problems(args.guide, args.installer, args.section)
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    for problem in found:
        print(problem, file=sys.stderr)
    print(f"check-install-guidance: {len(found)} violations", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())

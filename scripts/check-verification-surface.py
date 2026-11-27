#!/usr/bin/env python3
from __future__ import annotations

import argparse
import posixpath
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from lib.git_changes import GitError, tracked_files
from lib.proofs_settings import (
    PROOFS_FILE,
    SettingsError,
    load_proofs,
    matches,
    string,
    strings,
    table,
)

MAP_RULE = "SURFACE.feature-map"
JOURNEY_RULE = "SURFACE.journeys"
FENCE = re.compile(r"^(```|~~~)")
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)[^)]*\)")


@dataclass(frozen=True)
class Surface:
    feature_map: str
    sections: tuple[str, ...]
    lever_parts: tuple[str, ...]
    journeys: list[str]
    tests: list[str]


def load_surface(repo: Path) -> Surface:
    document = load_proofs(repo)
    surface = table(document, "surface")
    command = strings(surface, "lever_command", "surface")
    lever = string(surface, "lever_name", "surface")
    sections = (
        "Sub-features",
        "How to get to it (user POV)",
        f"Driving it with {lever}",
        "Gotchas",
    )
    return Surface(
        string(surface, "feature_map_path", "surface").rstrip("/"),
        sections,
        tuple(command[1:]),
        [string(surface, "journey_file_pattern", "surface")],
        strings(table(document, "layout"), "test_file_patterns", "layout"),
    )


def _headings(text: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    fenced = False
    for number, line in enumerate(text.splitlines(), start=1):
        if FENCE.match(line):
            fenced = not fenced
        elif not fenced and line.startswith("## "):
            found.append((number, line[3:].strip()))
    return found


def _links(text: str) -> list[tuple[int, str]]:
    return [
        (number, match.group(1))
        for number, line in enumerate(text.splitlines(), start=1)
        for match in LINK.finditer(line)
        if not re.match(r"^[a-z][a-z0-9+.-]*:|^#", match.group(1))
    ]


def _document_problems(repo: Path, path: str, surface: Surface) -> list[str]:
    text = (repo / path).read_text(encoding="utf-8")
    headings = _headings(text)
    problems: list[str] = []
    if tuple(title for _, title in headings) != surface.sections:
        found = ", ".join(title for _, title in headings) or "none"
        problems.append(
            f"{path}: {MAP_RULE}: H2 sections are [{found}]; use exactly "
            f"[{', '.join(surface.sections)}] in that order"
        )
    targets: list[str] = []
    for number, link in _links(text):
        target = posixpath.normpath(posixpath.join(posixpath.dirname(path), link.split("#", 1)[0]))
        if target.startswith("../") or not (repo / target).exists():
            problems.append(
                f"{path}:{number}: {MAP_RULE}: link {link} does not resolve; fix or remove it"
            )
        else:
            targets.append(target)
    if not any(matches(target, [*surface.tests, *surface.journeys]) for target in targets):
        problems.append(
            f"{path}: {MAP_RULE}: links no outcome test or journey; link the tests that prove it"
        )
    return problems


def feature_map_problems(repo: Path, files: list[str], surface: Surface) -> list[str]:
    documents = [
        path
        for path in files
        if path.startswith(f"{surface.feature_map}/") and path.endswith(".md")
    ]
    if not documents:
        return [
            f"{surface.feature_map}: {MAP_RULE}: no feature documents; add one document per feature"
        ]
    return [problem for path in documents for problem in _document_problems(repo, path, surface)]


def _drives_lever(path: Path, surface: Surface) -> bool:
    text = path.read_text(encoding="utf-8")
    return all(part in text for part in surface.lever_parts)


def journey_problems(repo: Path, files: list[str], surface: Surface) -> list[str]:
    problems: list[str] = []
    for path in files:
        if matches(path, surface.journeys):
            text = (repo / path).read_text(encoding="utf-8")
            if not all(part in text for part in surface.lever_parts):
                problems.append(
                    f"{path}: {JOURNEY_RULE}: journey does not drive the lever; run "
                    f"{' '.join(surface.lever_parts)} from it"
                )
        elif matches(path, surface.tests) and _drives_lever(repo / path, surface):
            problems.append(
                f"{path}: {JOURNEY_RULE}: drives the lever outside the journey pattern; "
                f"move it to a path matching {surface.journeys[0]}"
            )
    return problems


def check(repo: Path, paths: list[str]) -> list[str]:
    surface = load_surface(repo)
    files = paths or [path for path in tracked_files(repo) if (repo / path).is_file()]
    return [*feature_map_problems(repo, files, surface), *journey_problems(repo, files, surface)]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="check-verification-surface.py")
    parser.add_argument("--repo", type=Path, default=Path())
    parser.add_argument("paths", nargs="*")
    arguments = parser.parse_args(argv)
    try:
        problems = check(arguments.repo, arguments.paths)
    except (SettingsError, GitError, OSError) as error:
        print(
            f"{PROOFS_FILE}: {MAP_RULE}: {error}; fix the setting or repository state",
            file=sys.stderr,
        )
        return 1
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

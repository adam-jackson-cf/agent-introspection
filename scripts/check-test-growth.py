#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from lib import bun_adapter, filesystem, pytest_adapter
from lib.git_changes import GitError, changes_since, file_at, files_at, resolve, tracked_files
from lib.proofs_settings import (
    PROOFS_FILE,
    SettingsError,
    load_proofs,
    matches,
    string,
    strings,
    table,
)
from lib.run_evidence import EvidenceError, StaticCase

GROWTH_RULE = "TEST.growth.justified"
DUPLICATE_RULE = "TEST.duplicate-path"
STOP_WORDS = frozenset({"test", "case", "todo", "tbd", "placeholder", "example"})


@dataclass(frozen=True)
class Layout:
    patterns: list[str]
    fixtures_dir: str

    def owns(self, path: str) -> bool:
        inside_fixtures = path.startswith(f"{self.fixtures_dir.rstrip('/')}/")
        return matches(path, self.patterns) and not inside_fixtures


@dataclass(frozen=True)
class Collection:
    cases: list[StaticCase]
    unparsable: dict[str, str]


def load_layout(repo: Path) -> Layout:
    layout = table(load_proofs(repo), "layout")
    return Layout(
        strings(layout, "test_file_patterns", "layout"), string(layout, "fixtures_dir", "layout")
    )


def collect(root: Path, files: list[str]) -> Collection:
    cases: list[StaticCase] = []
    unparsable: dict[str, str] = {}
    for path in (item for item in files if item.endswith(".py")):
        try:
            cases.extend(pytest_adapter.static_cases(path, filesystem.read_text(root / path)))
        except (SyntaxError, UnicodeDecodeError) as error:
            unparsable[path] = str(error)
    bun_cases, bun_unparsable = bun_adapter.static_cases(
        root, [item for item in files if item.endswith((".ts", ".tsx"))]
    )
    return Collection([*cases, *bun_cases], {**unparsable, **bun_unparsable})


def _base_collection(repo: Path, base: str, layout: Layout) -> Collection:
    with tempfile.TemporaryDirectory() as raw:
        snapshot = Path(raw)
        files = [path for path in files_at(repo, base) if layout.owns(path)]
        for path in files:
            target = snapshot / path
            filesystem.mkdir(target.parent, parents=True, exist_ok=True)
            filesystem.write_text(target, file_at(repo, base, path) or "")
        return collect(snapshot, files)


def _words(case: StaticCase) -> list[str]:
    split = (
        pytest_adapter.behaviour_words if case.file.endswith(".py") else bun_adapter.behaviour_words
    )
    return [word for word in split(case.name) if word.lower() not in STOP_WORDS]


def _growth(new: list[StaticCase]) -> list[str]:
    return [
        f"{case.file}:{case.line}: {GROWTH_RULE}: {case.id} has no descriptive behaviour name "
        f"({case.name!r}); rename it to the behaviour it proves in two or more words, or remove it"
        for case in new
        if len(_words(case)) < 2
    ]


def _duplicates(new: list[StaticCase], current: list[StaticCase]) -> list[str]:
    by_fingerprint: dict[str, list[str]] = {}
    for case in current:
        by_fingerprint.setdefault(case.fingerprint, []).append(case.id)
    new_ids = {case.id for case in new}
    problems: list[str] = []
    for case in new:
        others = [other for other in by_fingerprint[case.fingerprint] if other != case.id]
        others = [other for other in others if other not in new_ids or other > case.id]
        if others:
            problems.append(
                f"{case.file}:{case.line}: {DUPLICATE_RULE}: {case.id} duplicates {others[0]} "
                "(same normalised calls and assertions); merge the two tests or remove one"
            )
    return problems


def check(repo: Path, base: str | None, paths: list[str]) -> list[str]:
    layout = load_layout(repo)
    current_files = [
        path
        for path in tracked_files(repo)
        if layout.owns(path) and filesystem.is_file(repo / path)
    ]
    current = collect(repo, current_files)
    if base is None:
        known: set[str] = set()
        changed = set(current_files)
    else:
        commit = resolve(repo, base)
        known = {case.id for case in _base_collection(repo, commit, layout).cases}
        changed = {change.path for change in changes_since(repo, commit)}
    scope = set(paths) if paths else None
    new = [
        case
        for case in current.cases
        if case.id not in known and (scope is None or case.file in scope)
    ]
    problems = [
        f"{path}: {GROWTH_RULE}: changed test file cannot be collected ({error}); fix its syntax"
        for path, error in sorted(current.unparsable.items())
        if path in changed
    ]
    return [*problems, *_growth(new), *_duplicates(new, current.cases)]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="check-test-growth.py")
    parser.add_argument("--repo", type=Path, default=Path())
    parser.add_argument("--base")
    parser.add_argument("paths", nargs="*")
    arguments = parser.parse_args(argv)
    try:
        problems = check(arguments.repo, arguments.base, arguments.paths)
    except (SettingsError, GitError, EvidenceError) as error:
        print(
            f"{PROOFS_FILE}: {GROWTH_RULE}: {error}; fix the setting or repository state",
            file=sys.stderr,
        )
        return 1
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

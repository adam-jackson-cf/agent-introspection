#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lib.git_changes import (
    DiffBase,
    GitError,
    add_base_arguments,
    changes_since,
    required_diff_base,
)
from lib.proofs_settings import (
    PROOFS_FILE,
    SettingsError,
    load_proofs,
    matches,
    string,
    strings,
    table,
)

RULE = "TEST.identity-artefacts"


def _test_paths(layout: dict[str, object]) -> list[str]:
    support = string(layout, "support_dir", "layout").rstrip("/")
    fixtures = string(layout, "fixtures_dir", "layout").rstrip("/")
    return [*strings(layout, "test_file_patterns", "layout"), f"{support}/**", f"{fixtures}/**"]


def check(repo: Path, base: DiffBase) -> list[str]:
    document = load_proofs(repo)
    artefacts = table(document, "identity_artefacts")
    reasons = {pattern: str(reason) for pattern, reason in artefacts.items()}
    if not reasons:
        raise SettingsError("[identity_artefacts] declares no artefacts")
    tests = _test_paths(table(document, "layout"))
    changed = {
        path
        for change in changes_since(repo, base.commit, staged=base.staged)
        for path in (change.path, change.old_path)
        if path
    }
    touched = sorted(path for path in changed if matches(path, list(reasons)))
    others = changed.difference(touched)
    if not touched or not others or not all(matches(path, tests) for path in others):
        return []
    return [
        f"{path}: {RULE}: test-only change edits an identity-bearing artefact "
        f"({next(reason for pattern, reason in reasons.items() if matches(path, [pattern]))}); "
        "revert it, or make the artefact change in its own production change"
        for path in touched
    ]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="check-identity-artefacts.py")
    parser.add_argument("--repo", type=Path, default=Path())
    add_base_arguments(parser, required=True)
    arguments = parser.parse_args(argv)
    try:
        problems = check(arguments.repo, required_diff_base(arguments.repo, arguments))
    except (SettingsError, GitError) as error:
        print(
            f"{PROOFS_FILE}: {RULE}: {error}; fix the setting or repository state", file=sys.stderr
        )
        return 1
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

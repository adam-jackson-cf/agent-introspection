#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lib.git_changes import GitError, added_lines, changes_since, resolve
from lib.proofs_settings import (
    PROOFS_FILE,
    SettingsError,
    load_proofs,
    matches,
    string,
    strings,
    table,
)

RULE = "TEST.golden-change-record"


def check(repo: Path, base: str) -> list[str]:
    golden = table(load_proofs(repo), "golden")
    globs = strings(golden, "globs", "golden")
    record = string(golden, "change_record", "golden")
    if not (repo / record).is_file():
        return [f"{PROOFS_FILE}: {RULE}: [golden] change_record {record} does not exist; create it"]
    commit = resolve(repo, base)
    changed = sorted({
        path
        for change in changes_since(repo, commit)
        for path in (change.path, change.old_path)
        if path
    })
    goldens = [path for path in changed if matches(path, globs)]
    entries = "\n".join(text for _, text in added_lines(repo, commit, [record]).get(record, []))
    return [
        f"{path}: {RULE}: golden file changed with no new entry naming it in {record}; "
        f"add an entry to {record} that names {path} and says why it changed"
        for path in goldens
        if path not in entries
    ]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="check-golden-changes.py")
    parser.add_argument("--repo", type=Path, default=Path())
    parser.add_argument("--base", required=True)
    arguments = parser.parse_args(argv)
    try:
        problems = check(arguments.repo, arguments.base)
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

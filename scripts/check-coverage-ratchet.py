#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

from lib.git_changes import (
    DiffBase,
    GitError,
    add_base_arguments,
    added_lines,
    changes_since,
    file_at,
    head_text,
    required_diff_base,
)
from lib.proofs_settings import (
    PROOFS_FILE,
    SettingsError,
    approved_paths,
    string,
    table,
)

RULE = "TEST.coverage-ratchet"
CONFIG = "pyproject.toml"
OTHER_CONFIGS = (".coveragerc",)
DEFAULT_PRAGMA = r"#\s*(pragma|PRAGMA)[:\s]?\s*(no|NO)\s*(cover|COVER|branch|BRANCH)"
SCRIPT_PRAGMA = r"(istanbul|c8|v8|node:coverage)\s+ignore"
GROWING_LISTS = (
    ("run", "omit"),
    ("report", "omit"),
    ("report", "exclude_lines"),
    ("report", "exclude_also"),
)
FIXED_VALUES = (("run", "source"), ("run", "include"), ("report", "include"))


def coverage_table(text: str | None) -> dict[str, Any]:
    if text is None:
        return {}
    tool = tomllib.loads(text).get("tool", {})
    coverage = tool.get("coverage", {}) if isinstance(tool, dict) else {}
    return coverage if isinstance(coverage, dict) else {}


def _value(coverage: dict[str, Any], section: str, key: str) -> Any:
    part = coverage.get(section, {})
    return part.get(key) if isinstance(part, dict) else None


def config_changes(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    changes: list[str] = []
    floor_before, floor_after = (
        _value(before, "report", "fail_under"),
        _value(after, "report", "fail_under"),
    )
    if floor_before is not None and (floor_after is None or floor_after < floor_before):
        changes.append(
            f"[tool.coverage.report] fail_under lowered from {floor_before} to {floor_after}"
        )
    for section, key in GROWING_LISTS:
        gained = sorted(
            set(_value(after, section, key) or []) - set(_value(before, section, key) or [])
        )
        if gained:
            changes.append(f"[tool.coverage.{section}] {key} gained {', '.join(gained)}")
    changes.extend(
        f"[tool.coverage.{section}] {key} changed"
        for section, key in FIXED_VALUES
        if _value(before, section, key) != _value(after, section, key)
    )
    return changes


def pragma_patterns(coverage: dict[str, Any]) -> list[re.Pattern[str]]:
    configured = [
        *(_value(coverage, "report", "exclude_lines") or []),
        *(_value(coverage, "report", "exclude_also") or []),
    ]
    return [re.compile(pattern) for pattern in (DEFAULT_PRAGMA, SCRIPT_PRAGMA, *configured)]


def _proofs(text: str | None) -> dict[str, Any]:
    if text is None:
        raise SettingsError(f"{PROOFS_FILE} is missing")
    return tomllib.loads(text)


def _new_approval(head: dict[str, Any], repo: Path, commit: str) -> bool:
    base_text = file_at(repo, commit, PROOFS_FILE)
    base = approved_paths(tomllib.loads(base_text), RULE) if base_text else set()
    return CONFIG in approved_paths(head, RULE) - base


def check(repo: Path, base: DiffBase) -> list[str]:
    commit = base.commit
    proofs = _proofs(head_text(repo, base, PROOFS_FILE))
    fixtures = string(table(proofs, "layout"), "fixtures_dir", "layout").rstrip("/") + "/"
    before = coverage_table(file_at(repo, commit, CONFIG))
    after = coverage_table(head_text(repo, base, CONFIG))
    changes = changes_since(repo, commit, staged=base.staged)
    changed = [change.path for change in changes if change.status != "D"]
    problems = [
        f"{path}: {RULE}: coverage configuration file added or changed; keep coverage settings in "
        f"{CONFIG} [tool.coverage] and remove {path}"
        for path in changed
        if path in OTHER_CONFIGS
    ]
    if not _new_approval(proofs, repo, commit):
        problems.extend(
            f"{CONFIG}: {RULE}: {change} without approval; restore it, or add an owner-approved "
            f"[[exception]] for {CONFIG} to {PROOFS_FILE} in this change"
            for change in config_changes(before, after)
        )
    patterns = pragma_patterns(after)
    owned = [path for path in changed if not path.startswith(fixtures)]
    for path, lines in sorted(added_lines(repo, commit, owned, staged=base.staged).items()):
        problems.extend(
            f"{path}:{number}: {RULE}: added coverage exclusion pragma; cover the code with a test "
            "instead of excluding it"
            for number, text in lines
            if _excludes(text, patterns)
        )
    return problems


def _excludes(text: str, patterns: list[re.Pattern[str]]) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="check-coverage-ratchet.py")
    parser.add_argument("--repo", type=Path, default=Path())
    add_base_arguments(parser, required=True)
    arguments = parser.parse_args(argv)
    try:
        problems = check(arguments.repo, required_diff_base(arguments.repo, arguments))
    except (SettingsError, GitError, tomllib.TOMLDecodeError) as error:
        print(
            f"{PROOFS_FILE}: {RULE}: {error}; fix the setting or repository state", file=sys.stderr
        )
        return 1
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

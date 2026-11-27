#!/usr/bin/env python3
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from lib.proofs_settings import (
    SettingsError,
    changed_paths,
    matches_any,
    settings_table,
    string_list,
    string_value,
    tracked_files,
    under,
)

PLACEMENT = "TEST.placement"
NAMING = "NAME.test-files"
CONFTEST = "conftest.py"
WORD_SPLIT = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z])(?=[A-Z])")
PYTEST_PREFIX = "test_"
FIXTURE_ROOT = "repo"


@dataclass(frozen=True, slots=True)
class Layout:
    root: Path
    test_patterns: list[str]
    test_like_patterns: list[str]
    support_dir: str
    support_files: list[str]
    fixtures_dir: str
    owners: dict[str, str]
    ignored: frozenset[str]
    token_rules: dict[str, re.Pattern[str]]
    numbered_allowlist: frozenset[str]

    @property
    def test_root(self) -> str:
        return self.support_dir.rsplit("/", 1)[0]


def load_layout(root: Path) -> Layout:
    table = settings_table(root, "layout")
    names = table.get("test_names")
    if not isinstance(names, dict):
        raise SettingsError("proofs.toml: [layout.test_names] is missing; declare it")
    structure = settings_table(root, "structure")
    owners = structure.get("ownership_areas")
    if not isinstance(owners, dict) or not all(isinstance(v, str) for v in owners.values()):
        raise SettingsError("proofs.toml: [structure.ownership_areas] must map globs to areas")
    return Layout(
        root=root,
        test_patterns=string_list(table, "test_file_patterns"),
        test_like_patterns=string_list(table, "test_like_patterns"),
        support_dir=string_value(table, "support_dir"),
        support_files=string_list(table, "support_files"),
        fixtures_dir=string_value(table, "fixtures_dir"),
        owners=dict(owners),
        ignored=frozenset(string_list(names, "ignored", "layout.test_names")),
        token_rules={
            kind: re.compile(string_value(names, kind, "layout.test_names"))
            for kind in ("ticket", "version", "date", "trailing_number")
        },
        numbered_allowlist=frozenset(string_list(names, "numbered_allowlist", "layout.test_names")),
    )


def behaviour_stem(path: str) -> tuple[str, str, str]:
    name = PurePosixPath(path).name
    if name.endswith(".py"):
        stem = name.removesuffix(".py")
        return stem.removeprefix(PYTEST_PREFIX), PYTEST_PREFIX, ".py"
    stem, _, suffix = name.partition(".")
    return stem, "", f".{suffix}"


def _token_kind(layout: Layout, token: str) -> str | None:
    lowered = token.lower()
    if lowered in layout.ignored or lowered in layout.numbered_allowlist:
        return None
    for kind, pattern in layout.token_rules.items():
        if pattern.match(lowered):
            return kind
    return None


def naming_problems(layout: Layout, path: str) -> list[str]:
    if under(path, layout.fixtures_dir):
        return []
    test_like = matches_any(path, layout.test_like_patterns)
    is_test = matches_any(path, layout.test_patterns)
    if test_like and not is_test:
        return [
            f"{path}: {NAMING}: looks like a test but matches no [layout] test_file_patterns; "
            f"rename it to match one of {', '.join(layout.test_patterns)}"
        ]
    if not is_test:
        return []
    stem, prefix, suffix = behaviour_stem(path)
    parts = [part for part in WORD_SPLIT.split(stem) if part]
    flagged = [(part, kind) for part in parts for kind in [_token_kind(layout, part)] if kind]
    if not flagged:
        return []
    kept = [part for part in parts if _token_kind(layout, part) is None]
    separator = "_" if suffix == ".py" else "-"
    corrected = PurePosixPath(path).with_name(f"{prefix}{separator.join(kept)}{suffix}")
    tokens = ", ".join(f"{part} ({kind})" for part, kind in flagged)
    return [
        f"{path}: {NAMING}: the name carries {tokens}; rename it for the behaviour it checks, "
        f"e.g. {corrected.as_posix()}"
    ]


def placement_problems(layout: Layout, path: str) -> list[str]:
    if under(path, layout.fixtures_dir):
        return []
    is_test = matches_any(path, layout.test_patterns)
    name = PurePosixPath(path).name
    in_root = under(path, layout.test_root)
    support = under(path, layout.support_dir) or path in layout.support_files
    problems: list[str] = []
    if name == CONFTEST and not in_root:
        problems.append(
            f"{path}: {PLACEMENT}: shared pytest fixtures live under {layout.test_root}/; "
            f"move it to {layout.test_root}/{CONFTEST}"
        )
    elif in_root and not (is_test or support or name == CONFTEST):
        problems.append(
            f"{path}: {PLACEMENT}: only tests, {CONFTEST}, support code and fixtures live under "
            f"{layout.test_root}/; move shared code to {layout.support_dir}/ or data to "
            f"{layout.fixtures_dir}/"
        )
    elif support and matches_any(path, [*layout.test_patterns, *layout.test_like_patterns]):
        problems.append(
            f"{path}: {PLACEMENT}: a test file sits in the support layout; move it to "
            f"{layout.test_root}/ beside the tests of its owner"
        )
    if is_test and not matches_any(path, layout.owners):
        problems.append(
            f"{path}: {PLACEMENT}: no [structure.ownership_areas] entry maps this test to an "
            "owner; add its glob and owning area, or move it beside its owner's tests"
        )
    return problems


def judged_path(layout: Layout, raw: str) -> str:
    path = Path(raw)
    if path.is_absolute() and path.resolve().is_relative_to(layout.root.resolve()):
        raw = path.resolve().relative_to(layout.root.resolve()).as_posix()
    parts = PurePosixPath(raw).parts
    if under(raw, layout.fixtures_dir) and FIXTURE_ROOT in parts:
        index = len(parts) - 1 - parts[::-1].index(FIXTURE_ROOT)
        return PurePosixPath(*parts[index + 1 :]).as_posix()
    return raw


def select_paths(layout: Layout, argv: list[str]) -> list[str]:
    paths: list[str]
    if argv and argv[0] in {"--staged", "--merge-base"}:
        paths = changed_paths(layout.root, argv[0], "AR")
    elif argv:
        paths = [judged_path(layout, raw) for raw in argv]
    else:
        paths = tracked_files(layout.root, ["**"])
    return paths


def main(argv: list[str]) -> int:
    try:
        layout = load_layout(Path.cwd())
        paths = select_paths(layout, argv)
    except (SettingsError, subprocess.CalledProcessError, OSError, re.error) as error:
        print(f"{PLACEMENT}: {error}", file=sys.stderr)
        return 1
    problems = [
        problem
        for path in paths
        for problem in (*naming_problems(layout, path), *placement_problems(layout, path))
    ]
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"check-test-placement: {len(paths)} paths, {len(problems)} violations", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

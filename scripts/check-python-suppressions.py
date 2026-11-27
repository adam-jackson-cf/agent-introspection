#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import re
import sys
import tokenize
from collections.abc import Iterable
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from lib.python_tool_scope import as_list, load_pyproject, maintained_python_files

_DIRECTIVE = re.compile(
    r"#\s*(?P<text>(?:ruff:\s*noqa|noqa|type:\s*ignore|mypy:\s*ignore-errors)\b[^#]*)",
    re.IGNORECASE,
)
_IGNORES = "RUFF.IGNORES"
_EXCLUDE = "RUFF.ROOT.exclude"
_RUFF_LIST_SETTINGS = ("ignore", "extend-ignore")
_RUFF_MAP_SETTINGS = ("per-file-ignores", "extend-per-file-ignores")
_RUFF_EXCLUDES = (
    ("ruff", "exclude"),
    ("ruff", "extend-exclude"),
    ("ruff.lint", "exclude"),
    ("ruff.format", "exclude"),
)
_MYPY_SILENCERS = ("disable_error_code", "ignore_errors", "ignore_missing_imports")
_FIX_IGNORE = "fix the findings it hides and delete the entry"


def comment_findings(relpath: str, source: str) -> list[str]:
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError) as error:
        return [f"{relpath}: {_IGNORES}: cannot be tokenized ({error}); fix the syntax error"]
    return [
        f"{relpath}:{token.start[0]}: {_IGNORES}: suppression comment "
        f"'# {' '.join(match.group('text').split())}'; fix the finding and delete the comment"
        for token in tokens
        if token.type == tokenize.COMMENT
        for match in _DIRECTIVE.finditer(token.string)
    ]


def _line(lines: list[str], needle: str) -> str:
    number = next((index for index, text in enumerate(lines, 1) if needle in text), None)
    return "" if number is None else f":{number}"


def _ruff_entries(ruff: dict[str, Any]) -> list[tuple[str, str]]:
    lint = ruff.get("lint", {})
    listed = [
        (f'"{code}"', f"[tool.ruff.lint] {key} lists {code}")
        for key in _RUFF_LIST_SETTINGS
        for code in as_list(lint.get(key))
    ]
    mapped = [
        (key, pattern, codes)
        for key in _RUFF_MAP_SETTINGS
        for pattern, codes in lint.get(key, {}).items()
    ]
    return listed + [
        (f'"{pattern}"', f'[tool.ruff.lint.{key}] "{pattern}" ignores {code}')
        for key, pattern, codes in mapped
        for code in as_list(codes)
    ]


def _mypy_entries(mypy: dict[str, Any]) -> Iterable[tuple[str, str]]:
    sections = [("[tool.mypy]", mypy)]
    sections.extend(
        (f"[[tool.mypy.overrides]] module {as_list(override.get('module'))}", override)
        for override in mypy.get("overrides", [])
    )
    for label, section in sections:
        for key in _MYPY_SILENCERS:
            if section.get(key) not in (None, False, []):
                yield key, f"{label} sets {key} = {section[key]!r}"


def config_findings(config: Path, document: dict[str, Any]) -> list[str]:
    lines = config.read_text(encoding="utf-8").splitlines()
    tool = document.get("tool", {})
    entries = [*_ruff_entries(tool.get("ruff", {})), *_mypy_entries(tool.get("mypy", {}))]
    return [
        f"{config.name}{_line(lines, needle)}: {_IGNORES}: {problem}; {_FIX_IGNORE}"
        for needle, problem in entries
    ]


def ruff_pattern_matches(pattern: str, relpath: str) -> bool:
    trimmed = pattern.rstrip("/").removeprefix("./")
    parts = relpath.split("/")
    if "/" not in trimmed:
        return any(fnmatchcase(part, trimmed) for part in parts)
    prefixes = ("/".join(parts[:end]) for end in range(1, len(parts) + 1))
    return any(fnmatchcase(prefix, trimmed) for prefix in prefixes)


def _section(tool: dict[str, Any], dotted: str) -> dict[str, Any]:
    section = tool
    for name in dotted.split("."):
        section = section.get(name, {})
    return section


def _exclude_patterns(tool: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        (f"[tool.{dotted}] {key}", pattern)
        for dotted, key in _RUFF_EXCLUDES
        for pattern in as_list(_section(tool, dotted).get(key))
    ]


def exclude_findings(config: Path, document: dict[str, Any], maintained: list[str]) -> list[str]:
    lines = config.read_text(encoding="utf-8").splitlines()
    return [
        f'{config.name}{_line(lines, f'"{pattern}"')}: {_EXCLUDE}: {setting} "{pattern}" hides '
        f"maintained file {path}; narrow the pattern to non-maintained paths"
        for setting, pattern in _exclude_patterns(document.get("tool", {}))
        for path in maintained
        if ruff_pattern_matches(pattern, path)
    ]


def repository_findings(config: Path) -> list[str]:
    root = config.parent
    document = load_pyproject(config)
    maintained = maintained_python_files(root)
    findings = config_findings(config, document)
    findings.extend(exclude_findings(config, document, maintained))
    for relpath in maintained:
        findings.extend(comment_findings(relpath, (root / relpath).read_text(encoding="utf-8")))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args(argv)
    if args.paths:
        findings = [
            finding
            for path in args.paths
            for finding in comment_findings(path.as_posix(), path.read_text(encoding="utf-8"))
        ]
    else:
        findings = repository_findings(args.config.resolve())
    for finding in findings:
        print(finding, file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())

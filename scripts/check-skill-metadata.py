#!/usr/bin/env python3
"""Fail when agent skill metadata or linked support files are invalid (SKILL.metadata)."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_KEY_VALUE = re.compile(r"^([A-Za-z0-9_-]+):[ \t]*(.*)$")
_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|#|/)", re.IGNORECASE)
_SKILL_GLOB = ".agents/skills/*/SKILL.md"


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_frontmatter(text: str) -> dict[str, str] | None:
    """Return top-level scalar frontmatter keys, or None when the block is absent."""
    match = _FRONTMATTER.match(text)
    if match is None:
        return None
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key_value = _KEY_VALUE.match(line)
        if key_value is not None:
            fields[key_value.group(1)] = _unquote(key_value.group(2))
    return fields


def _linked_paths(text: str) -> list[str]:
    body = _FRONTMATTER.sub("", text, count=1)
    body = re.sub(r"```.*?```", "", body, flags=re.DOTALL)
    targets = (match.group(1).split("#", 1)[0] for match in _LINK.finditer(body))
    return [target for target in targets if target and not _EXTERNAL.match(target)]


def check_skill(path: Path) -> list[str]:
    """Return SKILL.metadata violations for one SKILL.md file."""
    problems: list[str] = []
    text = path.read_text(encoding="utf-8")
    fields = parse_frontmatter(text)
    if fields is None:
        return [f"{path}: SKILL.metadata missing YAML frontmatter"]
    name = fields.get("name", "")
    if name != path.parent.name:
        problems.append(
            f"{path}: SKILL.metadata name {name!r} does not match directory {path.parent.name!r}"
        )
    description = fields.get("description", "")
    if not description.lower().startswith("use when"):
        problems.append(f"{path}: SKILL.metadata description must start with 'Use when'")
    shared = fields.get("global")
    if shared is not None and shared not in {"true", "false"}:
        problems.append(f"{path}: SKILL.metadata 'global' must be a boolean, got {shared!r}")
    for target in _linked_paths(text):
        if not (path.parent / target).exists():
            problems.append(f"{path}: SKILL.metadata linked file missing: {target}")
    return problems


def _git_ls_files(glob: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "ls-files", "-z", "--", glob],
        check=True,
        stdout=subprocess.PIPE,
        timeout=30,
    )


def _tracked_skills() -> list[Path]:
    completed = _git_ls_files(_SKILL_GLOB)
    return [Path(raw.decode()) for raw in completed.stdout.split(b"\0") if raw]


def main(argv: list[str]) -> int:
    """Check explicit SKILL.md files, or every tracked skill when none are given."""
    paths = [Path(arg) for arg in argv] or _tracked_skills()
    problems = [problem for path in paths for problem in check_skill(path)]
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"check-skill-metadata: {len(paths)} skills, {len(problems)} violations", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

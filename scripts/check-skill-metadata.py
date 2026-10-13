#!/usr/bin/env python3
"""Fail when agent skill metadata or linked support files are invalid (SKILL.metadata)."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import yaml

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|#|/)", re.IGNORECASE)
_SKILL_GLOB = ".agents/skills/*/SKILL.md"


def parse_frontmatter(text: str) -> dict[str, object] | None:
    """Return the parsed YAML frontmatter mapping, or None when the block is absent.

    Raises ValueError when the block is not valid YAML or not a mapping.
    """
    match = _FRONTMATTER.match(text)
    if match is None:
        return None
    try:
        loaded = yaml.safe_load(match.group(1))
    except yaml.YAMLError as error:
        raise ValueError(f"invalid YAML frontmatter: {error}") from error
    if not isinstance(loaded, dict):
        raise ValueError("frontmatter is not a YAML mapping")
    return {str(key): value for key, value in loaded.items()}


def _linked_paths(text: str) -> list[str]:
    body = _FRONTMATTER.sub("", text, count=1)
    body = re.sub(r"```.*?```", "", body, flags=re.DOTALL)
    targets = (match.group(1).split("#", 1)[0] for match in _LINK.finditer(body))
    return [target for target in targets if target and not _EXTERNAL.match(target)]


def check_skill(path: Path) -> list[str]:
    """Return SKILL.metadata violations for one SKILL.md file."""
    problems: list[str] = []
    text = path.read_text(encoding="utf-8")
    try:
        fields = parse_frontmatter(text)
    except ValueError as error:
        return [f"{path}: SKILL.metadata {error}"]
    if fields is None:
        return [f"{path}: SKILL.metadata missing YAML frontmatter"]
    name = fields.get("name")
    if name != path.parent.name:
        problems.append(
            f"{path}: SKILL.metadata name {name!r} does not match directory {path.parent.name!r}"
        )
    description = fields.get("description")
    if not isinstance(description, str) or not description.lower().startswith("use when"):
        problems.append(f"{path}: SKILL.metadata description must start with 'Use when'")
    shared = fields.get("global")
    if shared is not None and not isinstance(shared, bool):
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

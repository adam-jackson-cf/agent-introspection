#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import yaml

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|#|/)", re.IGNORECASE)
_SKILL_GLOB = ".agents/skills/*/SKILL.md"
_RULE = "SKILL.metadata"
_SCHEMA_KEYWORDS = frozenset({"$schema", "type", "required", "properties", "additionalProperties"})
_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "boolean": bool,
    "integer": int,
    "array": list,
    "object": dict,
}


class SchemaError(ValueError):
    __slots__ = ()


def load_schema(path: Path) -> dict[str, object]:
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SchemaError(f"{path}: {_RULE}: cannot read schema: {error}") from error
    if not isinstance(schema, dict) or set(schema) - _SCHEMA_KEYWORDS:
        raise SchemaError(f"{path}: {_RULE}: schema may use only {sorted(_SCHEMA_KEYWORDS)}")
    properties = schema.get("properties")
    if (
        schema.get("type") != "object"
        or schema.get("additionalProperties") is not False
        or not isinstance(properties, dict)
        or not all(
            isinstance(spec, dict) and set(spec) == {"type"} and spec["type"] in _TYPES
            for spec in properties.values()
        )
        or not isinstance(schema.get("required"), list)
        or not set(schema["required"]) <= set(properties)
    ):
        raise SchemaError(
            f"{path}: {_RULE}: schema must be a closed object of typed properties; "
            "set additionalProperties to false and give each property a type"
        )
    return schema


def schema_problems(path: Path, fields: dict[str, object], schema: dict[str, object]) -> list[str]:
    properties = schema["properties"]
    required = schema["required"]
    assert isinstance(properties, dict)
    assert isinstance(required, list)
    problems = [
        f"{path}: {_RULE}: frontmatter lacks required field {name!r}; add it"
        for name in required
        if name not in fields
    ]
    for name, value in sorted(fields.items()):
        spec = properties.get(name)
        if spec is None:
            problems.append(
                f"{path}: {_RULE}: frontmatter field {name!r} is not in the schema; remove it"
            )
            continue
        expected = spec["type"]
        valid = isinstance(value, _TYPES[expected]) and not (
            expected == "integer" and isinstance(value, bool)
        )
        if not valid:
            problems.append(
                f"{path}: {_RULE}: frontmatter field {name!r} must be a {expected}; fix its value"
            )
    return problems


def parse_frontmatter(text: str) -> dict[str, object] | None:
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


def check_skill(path: Path, schema: dict[str, object]) -> list[str]:
    problems: list[str] = []
    text = path.read_text(encoding="utf-8")
    try:
        fields = parse_frontmatter(text)
    except ValueError as error:
        return [f"{path}: {_RULE}: {error}; fix the frontmatter YAML"]
    if fields is None:
        return [
            f"{path}: {_RULE}: missing YAML frontmatter; add a --- block with name and description"
        ]
    problems += schema_problems(path, fields, schema)
    name = fields.get("name")
    if name != path.parent.name:
        problems.append(
            f"{path}: {_RULE}: name {name!r} does not match directory {path.parent.name!r}; "
            f"set name to {path.parent.name!r}"
        )
    description = fields.get("description")
    if not isinstance(description, str) or not description.lower().startswith("use when"):
        problems.append(f"{path}: {_RULE}: description must start with 'Use when'; reword it")
    for target in _linked_paths(text):
        if not (path.parent / target).exists():
            problems.append(f"{path}: {_RULE}: linked file missing: {target}; fix the link")
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail when agent skill metadata is invalid.")
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args(argv)
    try:
        schema = load_schema(args.schema)
    except SchemaError as error:
        print(error, file=sys.stderr)
        return 1
    paths = args.paths or _tracked_skills()
    problems = [problem for path in paths for problem in check_skill(path, schema)]
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"check-skill-metadata: {len(paths)} skills, {len(problems)} violations", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())

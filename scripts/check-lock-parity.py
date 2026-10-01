#!/usr/bin/env python3
"""Fail when package manifests and tracked lockfiles drift (PARITY.lock-version).

Offline structural check. `uv lock --check` and `bun install --frozen-lockfile` in the
central runner are the authoritative resolver checks; this fails earlier with a named diagnostic.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

_TRAILING_COMMA = re.compile(r",(\s*[}\]])")
_REQUIREMENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _specifier(text: str) -> str:
    """Return a canonical comma-joined, sorted specifier string without whitespace."""
    return ",".join(
        sorted(part.strip().replace(" ", "") for part in text.split(",") if part.strip())
    )


def _marker(text: str) -> str:
    """Return an environment marker with quotes and whitespace normalised."""
    return " ".join(text.replace('"', "'").split())


def _requirement_map(requirements: list[str]) -> set[tuple[str, str, str, str]]:
    """Return ``(name, extras, specifier, marker)`` tuples for PEP 508 requirement strings."""
    found: set[tuple[str, str, str, str]] = set()
    for requirement in requirements:
        text, _, marker = requirement.partition(";")
        text = text.strip()
        match = _REQUIREMENT_NAME.match(text)
        if match is None:
            continue
        rest = text[match.end() :].strip()
        extras = ""
        if rest.startswith("["):
            raw, _, rest = rest[1:].partition("]")
            extras = ",".join(sorted(_normalise(extra.strip()) for extra in raw.split(",")))
        rest = rest.strip().removeprefix("(").removesuffix(")")
        found.add((_normalise(match.group(0)), extras, _specifier(rest), _marker(marker)))
    return found


def _locked_map(deps: list[dict[str, object]]) -> set[tuple[str, str, str, str]]:
    """Return ``(name, extras, specifier, marker)`` tuples for uv.lock requires-dist entries."""
    found: set[tuple[str, str, str, str]] = set()
    for dep in deps:
        extras_raw = dep.get("extras", [])
        extras = ",".join(
            sorted(_normalise(str(extra)) for extra in extras_raw)
            if isinstance(extras_raw, list)
            else []
        )
        found.add((
            _normalise(str(dep["name"])),
            extras,
            _specifier(str(dep.get("specifier", ""))),
            _marker(str(dep.get("marker", ""))),
        ))
    return found


def check_python(pyproject_path: Path) -> list[str]:
    """Compare pyproject.toml with the sibling uv.lock root package entry."""
    lock_path = pyproject_path.with_name("uv.lock")
    if not lock_path.is_file():
        return [f"{pyproject_path}: PARITY.lock-version uv.lock is missing"]
    project = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))["project"]
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    name = _normalise(project["name"])
    entries = [pkg for pkg in lock.get("package", []) if _normalise(pkg["name"]) == name]
    if len(entries) != 1:
        return [f"{lock_path}: PARITY.lock-version has no root package entry for {name}"]
    entry = entries[0]
    problems: list[str] = []
    if entry.get("version") != project.get("version"):
        problems.append(
            f"{lock_path}: PARITY.lock-version version {entry.get('version')!r} "
            f"!= pyproject {project.get('version')!r}"
        )
    declared = _requirement_map(project.get("dependencies", []))
    for group in (
        tomllib
        .loads(pyproject_path.read_text(encoding="utf-8"))
        .get("dependency-groups", {})
        .values()
    ):
        declared |= _requirement_map([item for item in group if isinstance(item, str)])
    metadata = entry.get("metadata", {})
    locked = _locked_map(metadata.get("requires-dist", []))
    for group in metadata.get("requires-dev", {}).values():
        locked |= _locked_map(group)
    if declared != locked:
        problems.append(
            f"{lock_path}: PARITY.lock-version dependency drift, "
            f"declared-only={sorted(declared - locked)} locked-only={sorted(locked - declared)}"
        )
    return problems


def check_bun(package_path: Path) -> list[str]:
    """Compare package.json with the root workspace entry in the sibling bun.lock."""
    lock_path = package_path.with_name("bun.lock")
    if not lock_path.is_file():
        return [f"{package_path}: PARITY.lock-version bun.lock is missing"]
    package = json.loads(package_path.read_text(encoding="utf-8"))
    lock = json.loads(_TRAILING_COMMA.sub(r"\1", lock_path.read_text(encoding="utf-8")))
    workspace = lock["workspaces"].get("", {})
    problems: list[str] = []
    if workspace.get("name") != package.get("name"):
        problems.append(
            f"{lock_path}: PARITY.lock-version workspace name differs from package.json"
        )
    if workspace.get("version") != package.get("version"):
        problems.append(
            f"{lock_path}: PARITY.lock-version workspace version differs from package.json"
        )
    for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        if workspace.get(section, {}) != package.get(section, {}):
            problems.append(f"{lock_path}: PARITY.lock-version {section} differ from package.json")
    return problems


def main(argv: list[str]) -> int:
    """Check named manifests, or pyproject.toml and dashboard/package.json by default."""
    manifests = [Path(arg) for arg in argv] or [
        Path("pyproject.toml"),
        Path("dashboard/package.json"),
    ]
    problems: list[str] = []
    for manifest in manifests:
        if manifest.name == "pyproject.toml":
            problems.extend(check_python(manifest))
        elif manifest.name == "package.json":
            problems.extend(check_bun(manifest))
        else:
            problems.append(f"{manifest}: PARITY.lock-version unsupported manifest")
    for problem in problems:
        print(problem, file=sys.stderr)
    print(
        f"check-lock-parity: {len(manifests)} manifests, {len(problems)} violations",
        file=sys.stderr,
    )
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

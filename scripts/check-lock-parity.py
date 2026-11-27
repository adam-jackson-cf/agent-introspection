#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

import yaml

from lib.bun_lock import BunLockError, read_bun_lock

_REQUIREMENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")
_PACKAGE_MANAGER = re.compile(r"^bun@(\d+\.\d+\.\d+)$")
_FOREIGN_LOCKS = frozenset({
    "bun.lockb",
    "npm-shrinkwrap.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
})
_PACKAGE_SECTIONS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
_LOCK = "PARITY.lock-version"
_MANAGER = "PARITY.package-manager"


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _specifier(text: str) -> str:
    return ",".join(
        sorted(part.strip().replace(" ", "") for part in text.split(",") if part.strip())
    )


def _marker(text: str) -> str:
    return " ".join(text.replace('"', "'").split())


def _requirement_map(requirements: list[str]) -> set[tuple[str, str, str, str]]:
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
    lock_path = pyproject_path.with_name("uv.lock")
    project = tomllib.loads(pyproject_path.read_text(encoding="utf-8")).get("project")
    if project is None:
        return []
    if not lock_path.is_file():
        return [f"{pyproject_path}: {_LOCK}: uv.lock is missing; run uv lock and commit it"]
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    name = _normalise(project["name"])
    entries = [pkg for pkg in lock.get("package", []) if _normalise(pkg["name"]) == name]
    if len(entries) != 1:
        return [f"{lock_path}: {_LOCK}: no root package entry for {name}; run uv lock"]
    entry = entries[0]
    problems: list[str] = []
    if entry.get("version") != project.get("version"):
        problems.append(
            f"{lock_path}: {_LOCK}: version {entry.get('version')!r} "
            f"!= pyproject {project.get('version')!r}; run uv lock"
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
            f"{lock_path}: {_LOCK}: dependency drift, "
            f"declared-only={sorted(declared - locked)} locked-only={sorted(locked - declared)}; "
            "run uv lock"
        )
    return problems


def _foreign_locks(package_path: Path) -> list[str]:
    return [
        f"{package_path.with_name(name)}: {_MANAGER}: {name} beside a bun manifest; "
        "delete it and keep bun.lock"
        for name in sorted(_FOREIGN_LOCKS)
        if package_path.with_name(name).exists()
    ]


def _manager_problems(package_path: Path, package: dict[str, object]) -> list[str]:
    manager = package.get("packageManager")
    if not isinstance(manager, str) or not _PACKAGE_MANAGER.match(manager):
        return [
            f"{package_path}: {_MANAGER}: packageManager {manager!r} is not an exact bun pin; "
            'declare "packageManager": "bun@<x.y.z>"'
        ]
    return []


def check_bun(package_path: Path) -> list[str]:
    package = json.loads(package_path.read_text(encoding="utf-8"))
    lock_path = package_path.with_name("bun.lock")
    problems = _foreign_locks(package_path)
    if not lock_path.is_file():
        if any(package.get(section) for section in _PACKAGE_SECTIONS):
            problems.append(
                f"{package_path}: {_LOCK}: bun.lock is missing; run bun install and commit it"
            )
        return problems
    problems += _manager_problems(package_path, package)
    try:
        lock = read_bun_lock(lock_path)
    except BunLockError as error:
        return [*problems, f"{lock_path}: {_LOCK}: {error}; regenerate it with bun install"]
    workspaces = lock.get("workspaces")
    workspace = workspaces.get("", {}) if isinstance(workspaces, dict) else {}
    for key in ("name", "version"):
        if workspace.get(key) != package.get(key):
            problems.append(
                f"{lock_path}: {_LOCK}: workspace {key} differs from package.json; run bun install"
            )
    for section in _PACKAGE_SECTIONS:
        if workspace.get(section, {}) != package.get(section, {}):
            problems.append(
                f"{lock_path}: {_LOCK}: {section} differ from package.json; run bun install"
            )
    return problems


def _pins(manifests: list[Path]) -> dict[str, str]:
    pins: dict[str, str] = {}
    for manifest in manifests:
        if not manifest.with_name("bun.lock").is_file():
            continue
        manager = json.loads(manifest.read_text(encoding="utf-8")).get("packageManager")
        match = _PACKAGE_MANAGER.match(manager) if isinstance(manager, str) else None
        if match:
            pins[manifest.as_posix()] = match.group(1)
    return pins


def _mise_problems(mise: Path, pins: dict[str, str]) -> list[str]:
    tools = tomllib.loads(mise.read_text(encoding="utf-8")).get("tools", {})
    pinned = tools.get("bun") if isinstance(tools, dict) else None
    if pinned is None:
        return []
    return [
        f"{mise}: {_MANAGER}: bun {pinned} differs from {manifest} packageManager bun@{version}; "
        f'set bun = "{version}"'
        for manifest, version in sorted(pins.items())
        if str(pinned) != version
    ] or (
        []
        if pins
        else [f"{mise}: {_MANAGER}: pins bun {pinned} but no bun manifest declares packageManager"]
    )


def _setup_bun_steps(workflow: Path) -> list[dict[str, object]]:
    data = yaml.safe_load(workflow.read_text(encoding="utf-8")) or {}
    jobs = data.get("jobs", {}) if isinstance(data, dict) else {}
    return [
        step
        for job in (jobs.values() if isinstance(jobs, dict) else [])
        if isinstance(job, dict)
        for step in job.get("steps", [])
        if isinstance(step, dict) and str(step.get("uses", "")).startswith("oven-sh/setup-bun@")
    ]


def _workflow_problems(workflow: Path, pins: dict[str, str]) -> list[str]:
    problems = []
    for step in _setup_bun_steps(workflow):
        options = step.get("with") or {}
        source = options.get("bun-version-file") if isinstance(options, dict) else None
        if source not in pins:
            first = sorted(pins)[0] if pins else "<package.json>"
            problems.append(
                f"{workflow}: {_MANAGER}: setup-bun does not read a pinned packageManager; "
                f"use bun-version-file: {first} instead of bun-version"
            )
    return problems


def _git_ls_files(pathspecs: list[str]) -> str:
    argv = ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--"]
    completed = subprocess.run(
        [*argv, *pathspecs], check=True, stdout=subprocess.PIPE, text=True, timeout=30
    )
    return completed.stdout


def _listed(paths: Sequence[str], excludes: Sequence[str]) -> list[Path]:
    output = _git_ls_files([*paths, *excludes])
    return [Path(item) for item in sorted(set(output.split("\0"))) if item]


def _is_workflow(path: Path) -> bool:
    return PurePosixPath(path.as_posix()).parent.as_posix().endswith(".github/workflows") and (
        path.suffix in {".yml", ".yaml"}
    )


def check_files(files: list[Path]) -> list[str]:
    problems: list[str] = []
    manifests = [path for path in files if path.name == "package.json"]
    for path in files:
        if path.name == "pyproject.toml":
            problems.extend(check_python(path))
        elif path.name == "package.json":
            problems.extend(check_bun(path))
        elif path.name in _FOREIGN_LOCKS and not path.with_name("package.json").is_file():
            problems.append(f"{path}: {_MANAGER}: {path.name} is a foreign lockfile; delete it")
    pins = _pins(manifests)
    if len(set(pins.values())) > 1:
        problems += [
            f"{manifest}: {_MANAGER}: bun@{version} differs from other manifests; use one pin"
            for manifest, version in sorted(pins.items())
        ]
    for path in files:
        if path.name == "mise.toml":
            problems.extend(_mise_problems(path, pins))
        elif _is_workflow(path):
            problems.extend(_workflow_problems(path, pins))
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail when manifests, locks and pins drift.")
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("paths", nargs="*", default=["."])
    args = parser.parse_args(argv)
    files = _listed(args.paths or ["."], args.exclude)
    problems = sorted(set(check_files(files)))
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"check-lock-parity: {len(files)} files, {len(problems)} violations", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())

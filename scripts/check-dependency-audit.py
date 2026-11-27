#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from lib.bun_lock import BunLockError, locked_versions, read_bun_lock
from lib.npm_semver import RangeError, fix_versions, parse_range, parse_version, satisfies
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

RULE = "SECURITY.dependency-audit"
_TIMEOUT_SECONDS = 100
_PIP_AUDIT = ["uvx", "pip-audit==2.10.1", "--strict", "--disable-pip", "--no-deps"]
_UV_EXPORT = [
    "uv",
    "export",
    "--locked",
    "--all-groups",
    "--all-extras",
    "--no-emit-project",
    "--format",
    "requirements-txt",
]
_BUN_AUDIT = ["bun", "audit", "--json"]
_ECOSYSTEMS = {"uv.lock": "PyPI", "bun.lock": "npm"}
_MANIFEST_LOCKS = {"pyproject.toml": "uv.lock", "package.json": "bun.lock"}
_FOREIGN_LOCKS = frozenset({
    "bun.lockb",
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Pipfile.lock",
    "pdm.lock",
    "Cargo.lock",
    "go.sum",
    "Gemfile.lock",
    "composer.lock",
    "packages.lock.json",
    "mix.lock",
    "Podfile.lock",
})
_FOREIGN_LOCK_PATTERN = re.compile(r"^(requirements.*\.txt|pylock(\..+)?\.toml)$")
_FOREIGN_MANIFESTS = frozenset({
    "Cargo.toml",
    "go.mod",
    "Gemfile",
    "Pipfile",
    "setup.py",
    "setup.cfg",
    "composer.json",
    "requirements.in",
    "environment.yml",
    "environment.yaml",
})
_PACKAGE_SECTIONS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_GHSA = re.compile(r"/advisories/(GHSA(?:-[0-9a-z]{4}){3})$")
_ENTRY_FIELDS = frozenset({
    "advisory",
    "package",
    "version",
    "lock",
    "reason",
    "exposure",
    "expires",
})
_BLOCKED_FIELDS = frozenset({"blocked_by", "constraint", "constraint_source"})
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PYPI_CONSTRAINT = re.compile(
    r"^(?P<blocker>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>\S+) requires "
    r"(?P<package>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?P<spec>[<>=!~].*)$"
)
_NPM_CONSTRAINT = re.compile(
    r"^(?P<blocker>@?[^@\s]+)@(?P<version>\S+) requires (?P<package>@?[^@\s]+)@(?P<spec>.+)$"
)


class AuditError(ValueError):
    __slots__ = ()


@dataclass(frozen=True, order=True)
class Advisory:
    lock: str
    ecosystem: str
    package: str
    version: str
    advisory: str
    aliases: tuple[str, ...]
    fixes: tuple[str, ...]

    def key(self) -> tuple[str, str, str, str]:
        return (self.advisory, self.package, self.version, self.lock)

    def render(self) -> str:
        return (
            f"{RULE} lock={self.lock} ecosystem={self.ecosystem} package={self.package} "
            f"version={self.version} advisory={self.advisory} aliases={','.join(self.aliases)} "
            f"fix={','.join(self.fixes)}"
        )


@dataclass(frozen=True, order=True)
class Failure:
    kind: str
    path: str
    reason: str

    def render(self) -> str:
        return f"{RULE} failure={self.kind} path={self.path} reason={' '.join(self.reason.split())}"


@dataclass
class Policy:
    path: str
    excluded: dict[str, str] = field(default_factory=dict)
    exceptions: list[object] = field(default_factory=list)
    failures: list[Failure] = field(default_factory=list)


@dataclass
class Scope:
    locks: list[str] = field(default_factory=list)
    manifests: list[str] = field(default_factory=list)
    failures: list[Failure] = field(default_factory=list)


def _run(argv: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, check=False, timeout=_TIMEOUT_SECONDS
    )


def load_policy(path: Path) -> Policy:
    policy = Policy(path.as_posix())
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        policy.failures.append(Failure("exception", policy.path, f"cannot read: {error}"))
        return policy
    if not isinstance(data, dict) or set(data) != {"excluded", "exceptions"}:
        reason = "must be an object with exactly the keys excluded and exceptions"
        policy.failures.append(Failure("exception", policy.path, reason))
        return policy
    excluded, exceptions = data["excluded"], data["exceptions"]
    if not isinstance(exceptions, list) or not isinstance(excluded, list):
        policy.failures.append(
            Failure("exception", policy.path, "excluded and exceptions must be lists")
        )
        return policy
    policy.exceptions = exceptions
    for index, entry in enumerate(excluded):
        if (
            not isinstance(entry, dict)
            or set(entry) != {"path", "origin"}
            or not all(isinstance(value, str) and value.strip() for value in entry.values())
        ):
            reason = f"excluded[{index}] needs exactly a nonblank path and origin"
            policy.failures.append(Failure("exception", policy.path, reason))
            continue
        policy.excluded[entry["path"]] = entry["origin"]
    return policy


def listed_files(paths: Sequence[str]) -> list[str]:
    argv = ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *paths]
    result = _run(argv)
    if result.returncode != 0:
        raise AuditError(f"git ls-files failed: {result.stderr.strip()}")
    return sorted({item for item in result.stdout.split("\0") if item})


def _manifest_has_dependencies(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if path.name == "pyproject.toml":
        data = tomllib.loads(text)
        project = data.get("project", {})
        groups = data.get("dependency-groups", {})
        return bool(
            project.get("dependencies")
            or any(project.get("optional-dependencies", {}).values())
            or any(groups.values())
        )
    package = json.loads(text)
    return any(package.get(section) for section in _PACKAGE_SECTIONS)


def _is_foreign_lock(name: str) -> bool:
    return name in _FOREIGN_LOCKS or bool(_FOREIGN_LOCK_PATTERN.match(name))


def _manifest_failure(manifest: str, present: set[str]) -> Failure | None:
    path = PurePosixPath(manifest)
    siblings = {
        PurePosixPath(item).name for item in present if PurePosixPath(item).parent == path.parent
    }
    if path.name in _FOREIGN_MANIFESTS:
        if any(_is_foreign_lock(name) or name in _ECOSYSTEMS for name in siblings):
            return None
        return Failure(
            "manifest-without-lock", manifest, f"{path.name} has no lock this audit reads"
        )
    lock = _MANIFEST_LOCKS[path.name]
    try:
        has_dependencies = _manifest_has_dependencies(Path(manifest))
    except (OSError, UnicodeError, ValueError) as error:
        return Failure("manifest-without-lock", manifest, f"cannot read manifest: {error}")
    if has_dependencies and lock not in siblings:
        return Failure(
            "manifest-without-lock", manifest, f"declares dependencies; commit {lock} beside it"
        )
    return None


def classify(files: Iterable[str]) -> Scope:
    present = set(files)
    scope = Scope()
    for item in sorted(present):
        name = PurePosixPath(item).name
        if name in _ECOSYSTEMS:
            scope.locks.append(item)
        elif _is_foreign_lock(name):
            reason = f"{name} is not a lock format this audit reads; use uv.lock or bun.lock"
            scope.failures.append(Failure("unknown-lock-format", item, reason))
        elif name in _MANIFEST_LOCKS or name in _FOREIGN_MANIFESTS:
            scope.manifests.append(item)
            failure = _manifest_failure(item, present)
            if failure is not None:
                scope.failures.append(failure)
    return scope


def routine_scope(files: list[str], policy: Policy) -> list[str]:
    for path in sorted(set(policy.excluded) - set(files)):
        policy.failures.append(
            Failure("exception", policy.path, f"excluded path is not listed: {path}")
        )
    return [item for item in files if item not in policy.excluded]


def _json_payload(text: str) -> object:
    lines = [line for line in _ANSI.sub("", text).splitlines() if line.strip()]
    if lines and lines[0].startswith("bun audit "):
        lines = lines[1:]
    try:
        return json.loads("\n".join(lines))
    except json.JSONDecodeError as error:
        raise AuditError(f"auditor output is not JSON: {error}") from error


def _text_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise AuditError("auditor list field is malformed")
    return tuple(value)


def _pypi_vulns(lock: str, dependency: object) -> list[Advisory]:
    if not isinstance(dependency, dict):
        raise AuditError("pip-audit dependency is not an object")
    name, version, vulns = (
        dependency.get("name"),
        dependency.get("version"),
        dependency.get("vulns", []),
    )
    if not isinstance(name, str) or not isinstance(version, str) or not isinstance(vulns, list):
        raise AuditError("pip-audit dependency lacks name, version or vulns")
    found = []
    for vuln in vulns:
        if not isinstance(vuln, dict) or not isinstance(vuln.get("id"), str) or not vuln["id"]:
            raise AuditError("pip-audit vulnerability lacks an id")
        aliases = sorted(set(_text_list(vuln.get("aliases", []))))
        fixes = sorted(set(_text_list(vuln.get("fix_versions", []))), key=Version)
        package = canonicalize_name(name)
        found.append(
            Advisory(lock, "PyPI", package, version, vuln["id"], tuple(aliases), tuple(fixes))
        )
    return found


def parse_pip_audit(lock: str, stdout: str) -> list[Advisory]:
    payload = _json_payload(stdout)
    dependencies = payload.get("dependencies") if isinstance(payload, dict) else None
    if not isinstance(dependencies, list):
        raise AuditError("pip-audit output has no dependencies list")
    advisories = {item for dependency in dependencies for item in _pypi_vulns(lock, dependency)}
    return sorted(advisories)


def _npm_advisory(item: object) -> tuple[str, tuple[str, ...], str]:
    if not isinstance(item, dict):
        raise AuditError("bun audit advisory is not an object")
    url, ranges = item.get("url"), item.get("vulnerable_versions")
    if not isinstance(url, str) or not isinstance(ranges, str):
        raise AuditError("bun audit advisory lacks url or vulnerable_versions")
    match = _GHSA.search(url)
    advisory = match.group(1) if match else f"NPM-{item.get('id')}"
    cwe = item.get("cwe", [])
    title = item.get("title", "")
    malicious = (isinstance(cwe, list) and "CWE-506" in cwe) or (
        isinstance(title, str) and title.startswith("Malware in ")
    )
    aliases = (f"MAL-{advisory}",) if malicious and not advisory.startswith("MAL-") else ()
    return advisory, aliases, ranges


def _matched_versions(package: str, text: str, versions: set[str]) -> list[Advisory]:
    try:
        ranges = parse_range(text)
        return [
            Advisory(
                "", "npm", package, raw, "", (), tuple(fix_versions(parse_version(raw), ranges))
            )
            for raw in sorted(versions)
            if satisfies(parse_version(raw), ranges)
        ]
    except RangeError as error:
        raise AuditError(f"{package}: {error}") from error


def _package_advisories(
    lock: str, package: str, items: object, versions: dict[str, set[str]]
) -> list[Advisory]:
    if not isinstance(items, list):
        raise AuditError(f"bun audit entry for {package} is not a list")
    found = []
    for item in items:
        advisory, aliases, text = _npm_advisory(item)
        matched = _matched_versions(package, text, versions.get(package, set()))
        if not matched:
            raise AuditError(f"{package} {advisory} matches no locked version")
        found += [
            Advisory(lock, "npm", package, entry.version, advisory, aliases, entry.fixes)
            for entry in matched
        ]
    return found


def parse_bun_audit(lock: str, stdout: str, versions: dict[str, set[str]]) -> list[Advisory]:
    payload = _json_payload(stdout)
    if not isinstance(payload, dict):
        raise AuditError("bun audit output is not an object")
    found = {
        advisory
        for package, items in payload.items()
        for advisory in _package_advisories(lock, str(package), items, versions)
    }
    return sorted(found)


def _audit_uv(lock: str) -> list[Advisory]:
    directory = Path(lock).parent
    with tempfile.TemporaryDirectory() as scratch:
        requirements = Path(scratch) / "requirements.txt"
        exported = _run([*_UV_EXPORT, "--output-file", str(requirements)], cwd=directory)
        if exported.returncode != 0:
            raise AuditError(f"uv export failed: {exported.stderr.strip()}")
        audited = _run([*_PIP_AUDIT, "--format", "json", "-r", str(requirements)])
    if audited.returncode not in {0, 1} or not audited.stdout.strip():
        raise AuditError(f"pip-audit exited {audited.returncode}: {audited.stderr.strip()}")
    advisories = parse_pip_audit(lock, audited.stdout)
    if audited.returncode == 1 and not advisories:
        raise AuditError(f"pip-audit failed without findings: {audited.stderr.strip()}")
    return advisories


def _audit_bun(lock: str) -> list[Advisory]:
    try:
        versions = locked_versions(read_bun_lock(Path(lock)))
    except BunLockError as error:
        raise AuditError(str(error)) from error
    audited = _run(_BUN_AUDIT, cwd=Path(lock).parent)
    if audited.returncode not in {0, 1} or not audited.stdout.strip():
        raise AuditError(f"bun audit exited {audited.returncode}: {audited.stderr.strip()}")
    advisories = parse_bun_audit(lock, audited.stdout, versions)
    if audited.returncode == 1 and not advisories:
        raise AuditError(f"bun audit failed without findings: {audited.stderr.strip()}")
    return advisories


def audit_lock(lock: str) -> tuple[list[Advisory], list[Failure]]:
    auditor = _audit_uv if PurePosixPath(lock).name == "uv.lock" else _audit_bun
    try:
        return auditor(lock), []
    except (AuditError, OSError, subprocess.TimeoutExpired) as error:
        return [], [Failure("auditor", lock, str(error))]


def _uv_versions(lock: Path) -> dict[str, set[str]]:
    data = tomllib.loads(lock.read_text(encoding="utf-8"))
    versions: dict[str, set[str]] = {}
    for package in data.get("package", []):
        if isinstance(package.get("name"), str) and isinstance(package.get("version"), str):
            versions.setdefault(canonicalize_name(package["name"]), set()).add(package["version"])
    return versions


def _lock_versions(lock: str) -> dict[str, set[str]]:
    if PurePosixPath(lock).name == "uv.lock":
        return _uv_versions(Path(lock))
    versions: dict[str, set[str]] = locked_versions(read_bun_lock(Path(lock)))
    return versions


def _constraint_problem(advisory: Advisory, entry: dict[str, str]) -> str | None:
    pattern = _PYPI_CONSTRAINT if advisory.ecosystem == "PyPI" else _NPM_CONSTRAINT
    match = pattern.match(entry["constraint"])
    blocker, _, blocker_version = entry["blocked_by"].rpartition("@")
    if match is None:
        return "constraint does not parse as '<blocker>==<version> requires <package><spec>'"
    if (match["blocker"], match["version"]) != (blocker, blocker_version):
        return "constraint does not name the blocked_by package and version"
    named = (
        canonicalize_name(match["package"]) if advisory.ecosystem == "PyPI" else match["package"]
    )
    if named != advisory.package:
        return f"constraint targets {match['package']}, not {advisory.package}"
    try:
        allowed = _allowed_fixes(advisory, match["spec"])
    except (InvalidSpecifier, InvalidVersion, RangeError) as error:
        return f"constraint does not parse: {error}"
    return f"constraint allows fixed version {allowed[0]}; upgrade instead" if allowed else None


def _allowed_fixes(advisory: Advisory, spec: str) -> list[str]:
    if advisory.ecosystem == "PyPI":
        specifier = SpecifierSet(spec)
        return [fix for fix in advisory.fixes if specifier.contains(Version(fix), prereleases=True)]
    ranges = parse_range(spec)
    return [fix for fix in advisory.fixes if satisfies(parse_version(fix), ranges)]


def _blocked_problem(advisory: Advisory, entry: dict[str, str]) -> str | None:
    source = urlparse(entry["constraint_source"])
    if source.scheme != "https" or not source.netloc:
        return "constraint_source must be an absolute HTTPS URL"
    blocker, _, version = entry["blocked_by"].rpartition("@")
    if not blocker or not version:
        return "blocked_by must be <package>@<exact-version>"
    try:
        locked = _lock_versions(advisory.lock)
    except (OSError, UnicodeError, ValueError) as error:
        return f"cannot read {advisory.lock}: {error}"
    name = canonicalize_name(blocker) if advisory.ecosystem == "PyPI" else blocker
    if version not in locked.get(name, set()):
        return f"blocker {entry['blocked_by']} is not locked in {advisory.lock}"
    return _constraint_problem(advisory, entry)


def _entry_problem(entry: object, today: dt.date) -> str | None:
    if not isinstance(entry, dict) or not all(
        isinstance(value, str) and value.strip() for value in entry.values()
    ):
        return "entry must be an object of nonblank strings"
    keys = set(entry)
    if keys not in (_ENTRY_FIELDS, _ENTRY_FIELDS | _BLOCKED_FIELDS):
        return (
            "entry fields must be exactly "
            + ", ".join(sorted(_ENTRY_FIELDS))
            + " (plus blocked-fix fields)"
        )
    if not _DATE.match(entry["expires"]):
        return "expires must be YYYY-MM-DD"
    try:
        expires = dt.date.fromisoformat(entry["expires"])
    except ValueError:
        return "expires is not a calendar date"
    return f"entry expired on {entry['expires']}" if expires < today else None


def _judge_entry(
    entry: object, reported: dict[tuple[str, str, str, str], Advisory], today: dt.date
) -> str | None:
    problem = _entry_problem(entry, today)
    if problem is not None or not isinstance(entry, dict):
        return problem
    advisory = reported.get((entry["advisory"], entry["package"], entry["version"], entry["lock"]))
    if advisory is None:
        return "entry matches no reported advisory"
    blocked = set(entry) >= _BLOCKED_FIELDS
    if advisory.fixes and not blocked:
        return f"advisory has fix versions {','.join(advisory.fixes)}; upgrade instead"
    if not advisory.fixes and blocked:
        return "advisory has no fix version; drop the blocked-fix fields"
    return _blocked_problem(advisory, entry) if blocked else None


def apply_exceptions(
    advisories: list[Advisory], policy: Policy, audited: set[str], today: dt.date
) -> tuple[list[Advisory], list[Failure]]:
    reported = {advisory.key(): advisory for advisory in advisories}
    suppressed: set[tuple[str, str, str, str]] = set()
    failures: list[Failure] = []
    for index, entry in enumerate(policy.exceptions):
        lock = entry.get("lock") if isinstance(entry, dict) else None
        if isinstance(lock, str) and lock not in audited:
            continue
        problem = _judge_entry(entry, reported, today)
        if problem is not None:
            failures.append(Failure("exception", policy.path, f"exceptions[{index}]: {problem}"))
        elif isinstance(entry, dict):
            suppressed.add((entry["advisory"], entry["package"], entry["version"], entry["lock"]))
    return [item for item in advisories if item.key() not in suppressed], failures


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit every tracked lock file for advisories.")
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args(argv)
    policy = load_policy(args.policy)
    try:
        files = listed_files(args.paths)
    except AuditError as error:
        print(Failure("auditor", ".", str(error)).render())
        return 1
    if not args.paths:
        files = routine_scope(files, policy)
    scope = classify(files)
    if args.paths and not scope.locks and not scope.manifests and not scope.failures:
        print(
            f"check-dependency-audit: no lock or manifest under {' '.join(args.paths)}",
            file=sys.stderr,
        )
        return 2
    advisories: list[Advisory] = []
    failures = [*policy.failures, *scope.failures]
    for lock in scope.locks:
        found, problems = audit_lock(lock)
        advisories += found
        failures += problems
    today = dt.datetime.now(dt.UTC).date()
    advisories, exception_failures = apply_exceptions(advisories, policy, set(scope.locks), today)
    rendered = [item.render() for item in advisories]
    rendered += [item.render() for item in [*failures, *exception_failures]]
    lines = sorted(set(rendered))
    for line in lines:
        print(line)
    print(
        f"check-dependency-audit: {len(scope.locks)} locks, {len(scope.manifests)} manifests, "
        f"{len(lines)} findings",
        file=sys.stderr,
    )
    return 1 if lines else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Any

USAGE = """Fail when this repository's proofs.toml claims are not enforced and proven.

Usage:
  python3 scripts/validate-rule-coverage.py --root <repo> [--proofs <file>] \\
      [--catalog <catalogue.json>] [--structure-only] [--live-host-query]

Reads proofs.toml (default <root>/proofs.toml) and the repo files it names. For every claim
it checks that each enforcement point's file invokes the control and runs every canary through
the enforcement entrypoint; it runs the hook installation check once. A [validator] CI job is
required when any claim names a ci point. A later point names no status, so it never sets a
required merge status. Command-evidence exclusions run every time, except host = true ones,
which run with host claims only under --live-host-query. With --catalog it also proves that
every catalogue rule is covered by exactly one claim or one approved exclusion of a conditional
rule. --structure-only checks every field and path without running canaries or the hook check.
Writes nothing.
Exit 0: prints proofs PASS, proofs PASS_WITH_APPROVED_EXCLUSIONS or proofs STRUCTURE_ONLY,
preceded by proofs HOST_NOT_QUERIED: <claim ids> when host checks were not queried.
Exit 1: prints every error as proofs FAIL: <error>. Exit 2: the root is missing.
"""

CONFIG_TABLES = frozenset({
    "structure",
    "layout",
    "test_budget",
    "python_quality",
    "surface",
    "identity_artefacts",
    "golden",
})
TOP_LEVEL = (
    frozenset({
        "schema",
        "scope",
        "validator",
        "hooks",
        "claim",
        "not_applicable",
        "exception",
    })
    | CONFIG_TABLES
)
FIXTURE = "{fixture}"
SCOPE_MODES = frozenset({"whole", "changed", "paths"})
EVIDENCE_POINTS = {"local_ci": "ci", "git_hook": "hook", "later_job": "later"}
GITHUB_REMOTE = re.compile(
    r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"([^/\s]+)/([^/\s]+?)(?:\.git)?/?$"
)


class CoverageError(ValueError):
    __slots__ = ()


def _text(value: object, owner: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CoverageError(f"{owner}: missing {label}")
    return value


def _argv(value: object, owner: str, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(part, str) and part.strip() for part in value)
    ):
        raise CoverageError(f"{owner}: {label} must be a nonempty argv array")
    return value


def _table(value: object, owner: str, label: str, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CoverageError(f"{owner}: missing {label} table")
    unknown = sorted(value.keys() - allowed)
    if unknown:
        raise CoverageError(f"{owner}: unknown {label} field: {', '.join(unknown)}")
    return value


def _repo_path(root: Path, value: object, owner: str, *, directory_ok: bool = False) -> Path:
    relative = _text(value, owner, "path")
    if Path(relative).is_absolute():
        raise CoverageError(f"{owner}: absolute paths are forbidden: {relative}")
    resolved = (root / relative).resolve()
    present = _is_file(resolved) or (directory_ok and _is_dir(resolved))
    if not resolved.is_relative_to(root) or not present:
        raise CoverageError(f"{owner}: missing or outside target path: {relative}")
    return resolved


def _contains(root: Path, path: object, text: object, owner: str, label: str) -> None:
    needle = _text(text, owner, label)
    if needle not in _repo_path(root, path, owner).read_text(encoding="utf-8"):
        raise CoverageError(f"{owner}: {path} lacks {label}: {needle}")


def _spawn(argv: list[str], root: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, cwd=root, capture_output=True, text=True, check=False, timeout=timeout
    )


def _run(argv: list[str], root: Path, owner: str, timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return _spawn(argv, root, timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CoverageError(f"{owner}: command could not complete: {argv[0]}") from exc


def _scope(proofs: dict[str, Any]) -> list[str]:
    scope = proofs.get("scope")
    if not isinstance(scope, dict) or scope.get("mode") not in SCOPE_MODES:
        return ["invalid scope: mode must be whole, changed or paths"]
    mode, base, paths = scope["mode"], scope.get("base_ref"), scope.get("paths")
    if mode == "changed" and (not isinstance(base, str) or not base.strip()):
        return ["invalid scope: changed mode needs base_ref"]
    if mode == "paths" and (
        not isinstance(paths, list)
        or not paths
        or not all(
            isinstance(p, str)
            and p
            and not PurePosixPath(p).is_absolute()
            and ".." not in PurePosixPath(p).parts
            for p in paths
        )
    ):
        return ["invalid scope: paths mode needs repo-relative paths without '..'"]
    return []


def _validator(root: Path, proofs: dict[str, Any]) -> str:
    table = _table(proofs.get("validator"), "validator", "validator", {"status", "file", "ref"})
    _contains(root, table.get("file"), table.get("ref"), "validator", "ref")
    return _text(table.get("status"), "validator", "status")


def _points(root: Path, claim_id: str, value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise CoverageError(f"{claim_id}: no enforcement point")
    points = []
    for item in value:
        point = _table(item, claim_id, "enforced_by", {"point", "file", "ref", "status"})
        kind = point.get("point")
        if kind not in {"hook", "ci", "later"}:
            raise CoverageError(f"{claim_id}: enforcement point must be hook, ci or later")
        if kind == "ci":
            _text(point.get("status"), claim_id, "ci status")
        elif "status" in point:
            raise CoverageError(f"{claim_id}: only a ci point names a status")
        _contains(root, point.get("file"), point.get("ref"), claim_id, "ref")
        points.append(point)
    return points


def _canary(root: Path, claim_id: str, value: object, *, run: bool) -> None:
    allowed = {"command", "passing", "violating", "expect", "timeout_seconds", "rule"}
    canary = _table(value, claim_id, "canary", allowed)
    if "rule" in canary:
        _text(canary["rule"], claim_id, "canary rule")
    command = _argv(canary.get("command"), claim_id, "canary command")
    if not any(FIXTURE in part for part in command):
        raise CoverageError(f"{claim_id}: canary command must use {{fixture}}")
    problems: list[str] = []
    passing = _guard(problems, _repo_path, root, canary.get("passing"), claim_id, directory_ok=True)
    violating = _guard(
        problems, _repo_path, root, canary.get("violating"), claim_id, directory_ok=True
    )
    if problems:
        raise CoverageError("; ".join(problems))
    expect = _text(canary.get("expect"), claim_id, "canary expect")
    timeout = canary.get("timeout_seconds", 60)
    if type(timeout) is not int or not 1 <= timeout <= 120:
        raise CoverageError(f"{claim_id}: invalid canary timeout")
    if not run:
        return
    for fixture, should_pass in ((passing, True), (violating, False)):
        args = [part.replace(FIXTURE, str(fixture)) for part in command]
        result = _run(args, root, claim_id, timeout)
        if should_pass and result.returncode != 0:
            raise CoverageError(f"{claim_id}: compliant canary failed")
        if not should_pass and result.returncode == 0:
            raise CoverageError(f"{claim_id}: violating canary passed")
        if not should_pass and expect not in result.stdout + result.stderr:
            raise CoverageError(f"{claim_id}: expected diagnostic missing: {expect}")


def _claims(
    root: Path, proofs: dict[str, Any], *, run: bool
) -> tuple[list[dict[str, Any]], list[str]]:
    claims, errors = proofs.get("claim"), []
    seen: set[str] = set()
    if not isinstance(claims, list) or not claims:
        return [], ["no claims"]
    parsed = []
    for index, claim in enumerate(claims):
        owner = claim.get("id") if isinstance(claim, dict) else None
        owner = owner if isinstance(owner, str) and owner.strip() else f"claim[{index}]"
        try:
            entry, problems = _claim(root, claim, owner, seen, run=run)
        except (CoverageError, UnicodeError) as exc:
            errors.append(str(exc))
            continue
        parsed.append(entry)
        errors += problems
    return parsed, errors


def _claim(
    root: Path, value: object, owner: str, seen: set[str], *, run: bool
) -> tuple[dict[str, Any], list[str]]:
    host = isinstance(value, dict) and "host" in value
    allowed = (
        {"id", "rules", "host"} if host else {"id", "rules", "control", "enforced_by", "canary"}
    )
    claim = _table(value, owner, "claim", allowed)
    claim_id = _text(claim.get("id"), owner, "claim id")
    if claim_id in seen:
        raise CoverageError(f"{claim_id}: duplicate claim id")
    seen.add(claim_id)
    rules = claim.get("rules")
    if (
        not isinstance(rules, list)
        or not rules
        or not all(isinstance(rule, str) and rule.strip() for rule in rules)
    ):
        raise CoverageError(f"{claim_id}: rules must be a nonempty list of rule IDs or patterns")
    if host:
        _host_shape(claim, claim_id)
        return claim, []
    problems = _claim_parts(root, claim, claim_id, rules, run=run)
    if problems:
        claim["broken"] = True
    return claim, problems


def _host_shape(claim: dict[str, Any], claim_id: str) -> None:
    host_table = _table(claim["host"], claim_id, "host", {"command", "expect"})
    _argv(host_table.get("command"), claim_id, "host command")
    expect = host_table.get("expect")
    if (
        not isinstance(expect, list)
        or not expect
        or not all(isinstance(item, str) and item.strip() for item in expect)
    ):
        raise CoverageError(f"{claim_id}: host expect must be a nonempty list")


def _claim_parts(
    root: Path, claim: dict[str, Any], claim_id: str, rules: list[str], *, run: bool
) -> list[str]:
    problems: list[str] = []
    controls = claim.get("control")
    if not isinstance(controls, list) or not controls:
        problems.append(f"{claim_id}: no control paths")
    for control in controls if isinstance(controls, list) else []:
        _guard(problems, _repo_path, root, control, claim_id, directory_ok=True)
    points = _guard(problems, _points, root, claim_id, claim.get("enforced_by"))
    claim["enforced_by"] = points if points is not None else []
    canaries = claim.get("canary")
    if not isinstance(canaries, list) or not canaries:
        problems.append(f"{claim_id}: canary must be one or more [[claim.canary]] tables")
    for canary in canaries if isinstance(canaries, list) else []:
        _guard(problems, _canary, root, claim_id, canary, run=run)
        named = canary.get("rule") if isinstance(canary, dict) else None
        if named and not any(fnmatchcase(named, rule) for rule in rules):
            problems.append(f"{claim_id}: canary rule not in claim rules: {named}")
    return problems


def _origin_repository(root: Path) -> str | None:
    try:
        result = _spawn(["git", "-C", str(root), "remote", "get-url", "origin"], root, 30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = GITHUB_REMOTE.match(result.stdout.strip()) if result.returncode == 0 else None
    return f"{match.group(1)}/{match.group(2)}".lower() if match else None


def _host(root: Path, claim: dict[str, Any], statuses: set[str], *, live: bool) -> None:
    claim_id, host = claim["id"], claim["host"]
    origin = _origin_repository(root)
    if origin and f"repos/{origin}/" not in " ".join(host["command"]).lower():
        raise CoverageError(f"{claim_id}: host command does not query origin {origin}")
    if not live:
        return
    result = _run(host["command"], root, claim_id, 60)
    if result.returncode != 0:
        raise CoverageError(f"{claim_id}: host query failed")
    for item in host["expect"]:
        if item not in result.stdout:
            raise CoverageError(f"{claim_id}: host query lacks: {item}")
    if any(fnmatchcase("GIT.required-checks", rule) for rule in claim["rules"]):
        for status in sorted(statuses):
            if f'"{status}"' not in result.stdout:
                raise CoverageError(f"{claim_id}: host does not require status: {status}")


def _hooks(root: Path, proofs: dict[str, Any], *, run: bool) -> None:
    hooks = _table(proofs.get("hooks"), "hooks", "hooks", {"check", "expect"})
    command = _argv(hooks.get("check"), "hooks", "check")
    expect = _text(hooks.get("expect"), "hooks", "expect")
    if not run:
        return
    result = _run(command, root, "hooks", 60)
    if result.returncode != 0:
        raise CoverageError("hooks: installation check failed")
    if expect not in result.stdout + result.stderr:
        raise CoverageError(f"hooks: installation check lacks: {expect}")


def _exclusion(root: Path, value: object, index: int, *, live: bool) -> dict[str, Any]:
    owner = f"not_applicable[{index}]"
    entry = _table(value, owner, "not_applicable", {"rule", "reason", "approved_by", "evidence"})
    owner = _text(entry.get("rule"), owner, "rule")
    _text(entry.get("reason"), owner, "not-applicable reason")
    _text(entry.get("approved_by"), owner, "not-applicable approval")
    evidence = entry.get("evidence")
    if isinstance(evidence, dict) and "path" in evidence:
        _table(evidence, owner, "evidence", {"path", "contains"})
        _contains(root, evidence["path"], evidence.get("contains"), owner, "evidence")
        return entry
    if isinstance(evidence, dict) and "absent" in evidence:
        _table(evidence, owner, "evidence", {"absent"})
        patterns = _argv(evidence["absent"], owner, "evidence absent patterns")
        found = _run(
            [
                "git",
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "--",
                *patterns,
            ],
            root,
            owner,
            60,
        )
        if found.returncode != 0 or found.stdout.strip():
            raise CoverageError(
                f"{owner}: construct present: {', '.join(found.stdout.split()[:3])}"
            )
        return entry
    evidence = _table(evidence, owner, "evidence", {"command", "expect", "host"})
    command = _argv(evidence.get("command"), owner, "evidence command")
    expect = _text(evidence.get("expect"), owner, "evidence expect")
    if evidence.get("host", False) not in {True, False}:
        raise CoverageError(f"{owner}: evidence host must be true or false")
    if live or not evidence.get("host", False):
        result = _run(command, root, owner, 60)
        if expect not in result.stdout + result.stderr:
            raise CoverageError(f"{owner}: evidence lacks: {expect}")
    return entry


def _exceptions(value: object) -> None:
    for index, item in enumerate(value if isinstance(value, list) else [value]):
        owner = f"exception[{index}]"
        allowed = {"rule", "path", "reason", "approved_by", "check"}
        entry = _table(item, owner, "exception", allowed)
        for field in ("rule", "path", "reason", "approved_by"):
            _text(entry.get(field), owner, field)
        if "check" in entry:
            _text(entry["check"], owner, "check")


def _shape(proofs: dict[str, Any]) -> list[str]:
    errors = [f"unknown top-level key: {key}" for key in sorted(proofs.keys() - TOP_LEVEL)]
    if proofs.get("schema") != 1:
        errors.append("schema must be 1")
    errors += [
        f"{key} must be a table"
        for key in sorted(CONFIG_TABLES & proofs.keys())
        if not isinstance(proofs[key], dict)
    ]
    return errors + _scope(proofs)


def _guard(errors: list[str], check: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return check(*args, **kwargs)
    except (CoverageError, UnicodeError) as exc:
        errors.append(str(exc))
        return None


def _coverage(
    catalog: dict[str, Any],
    claims: list[dict[str, Any]],
    exclusions: list[dict[str, Any]],
) -> list[str]:
    rows = _rows(catalog)
    errors: list[str] = []
    owners = [(rule, claim) for claim in claims for rule in claim["rules"]]
    owners += [(entry["rule"], entry) for entry in exclusions]
    for pattern in sorted({rule for rule, _ in owners}):
        if not any(fnmatchcase(rule_id, pattern) for rule_id in rows):
            errors.append(f"unknown rule pattern: {pattern}")
    for rule_id, row in sorted(rows.items()):
        matches = [owner for pattern, owner in owners if fnmatchcase(rule_id, pattern)]
        if len(matches) != 1:
            errors.append(
                f"{'missing rule' if not matches else 'rule covered more than once'}: {rule_id}"
            )
            continue
        if matches[0].get("broken"):
            continue
        errors += _fit(rule_id, row, matches[0])
        canaries = matches[0].get("canary", [])
        if (
            row.get("kind") == "bespoke"
            and "canary" in matches[0]
            and rule_id not in {item.get("rule") for item in canaries}
        ):
            errors.append(f"{rule_id}: claim {matches[0]['id']} has no canary for this rule")
    return errors


def _fit(rule_id: str, row: dict[str, Any], owner: dict[str, Any]) -> list[str]:
    if "reason" in owner:
        if row.get("applicability") != "conditional":
            return [f"{rule_id}: always-applicable rule cannot be excluded"]
        return []
    kind = row.get("evidence_kind", "local_ci")
    if kind == "host_query":
        return [] if "host" in owner else [f"{rule_id}: claim {owner['id']} lacks a host query"]
    point = EVIDENCE_POINTS[kind]
    if any(item["point"] == point for item in owner.get("enforced_by", [])):
        return []
    return [f"{rule_id}: claim {owner['id']} lacks a {point} enforcement point"]


def _rows(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if catalog.get("schema_version") != 1 or not isinstance(catalog.get("rules"), list):
        raise CoverageError("invalid catalog schema")
    rows: dict[str, dict[str, Any]] = {}
    for row in catalog["rules"]:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise CoverageError("invalid catalog rule")
        if row["id"] in rows:
            raise CoverageError(f"duplicate catalog rule: {row['id']}")
        if row.get("evidence_kind", "local_ci") not in {*EVIDENCE_POINTS, "host_query"}:
            raise CoverageError(f"{row['id']}: unknown evidence kind")
        rows[row["id"]] = row
    if not rows:
        raise CoverageError("empty catalog rule set")
    return rows


def validate(
    root: Path,
    proofs: dict[str, Any],
    catalog: dict[str, Any] | None = None,
    *,
    structure_only: bool,
    live_host_query: bool = False,
) -> tuple[list[str], list[str]]:
    errors = _shape(proofs)
    run = not structure_only
    claims, claim_errors = _claims(root, proofs, run=run)
    errors += claim_errors
    hosts = [claim for claim in claims if "host" in claim]
    points = [point for claim in claims for point in claim.get("enforced_by", [])]
    if any(point["point"] == "hook" for point in points):
        _guard(errors, _hooks, root, proofs, run=run)
    statuses = {point["status"] for point in points if point["point"] == "ci"}
    if "validator" in proofs:
        validator_status = _guard(errors, _validator, root, proofs)
        statuses |= {validator_status} if validator_status else set()
    elif statuses:
        errors.append("validator: required when a claim names a ci point")
    for claim in hosts:
        _guard(errors, _host, root, claim, statuses, live=live_host_query)
    entries = proofs.get("not_applicable", [])
    exclusions = [
        entry
        for index, item in enumerate(entries if isinstance(entries, list) else [entries])
        if (entry := _guard(errors, _exclusion, root, item, index, live=live_host_query))
    ]
    if "exception" in proofs:
        _guard(errors, _exceptions, proofs["exception"])
    if catalog is not None:
        errors += _guard(errors, _coverage, catalog, claims, exclusions) or []
    unqueried = [] if live_host_query else [claim["id"] for claim in hosts]
    unqueried += [
        f"not_applicable:{entry['rule']}"
        for entry in exclusions
        if not live_host_query and entry["evidence"].get("host") is True
    ]
    return sorted(errors), unqueried


def _is_file(path: Path) -> bool:
    return path.is_file()


def _is_dir(path: Path) -> bool:
    return path.is_dir()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        data: dict[str, Any] = tomllib.load(handle)
        return data


def _load(path: Path) -> dict[str, Any]:
    try:
        return _read_toml(path)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise CoverageError(f"cannot read proofs file: {path.name}: {exc}") from exc


def _catalog(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(_read_text(path))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CoverageError(f"cannot read catalog: {path.name}") from exc
    if not isinstance(data, dict):
        raise CoverageError(f"expected JSON object: {path.name}")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=USAGE)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--proofs", type=Path, help="default: <root>/proofs.toml")
    parser.add_argument("--catalog", type=Path, help="prove catalogue coverage (skill runs)")
    parser.add_argument("--structure-only", action="store_true")
    parser.add_argument(
        "--live-host-query",
        action="store_true",
        help="run host claims and host command-evidence exclusions live",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if not _is_dir(root):
        print("proofs FAIL: target root is missing", file=sys.stderr)
        return 2
    try:
        proofs = _load(args.proofs or root / "proofs.toml")
        catalog = _catalog(args.catalog) if args.catalog else None
        errors, unqueried = validate(
            root,
            proofs,
            catalog,
            structure_only=args.structure_only,
            live_host_query=args.live_host_query,
        )
    except CoverageError as exc:
        errors, unqueried = [str(exc)], []
    if errors:
        for error in errors:
            print(f"proofs FAIL: {error}", file=sys.stderr)
        return 1
    if unqueried:
        print(f"proofs HOST_NOT_QUERIED: {', '.join(unqueried)}")
    exclusions = proofs.get("not_applicable", [])
    result = (
        "STRUCTURE_ONLY"
        if args.structure_only
        else "PASS_WITH_APPROVED_EXCLUSIONS"
        if exclusions
        else "PASS"
    )
    print(f"proofs {result}: {len(proofs['claim'])} claims, {len(exclusions)} exclusions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

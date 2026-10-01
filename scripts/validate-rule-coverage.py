#!/usr/bin/env python3
"""Fail when a target omits or cannot prove an Enaible guardrail rule."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


class CoverageError(ValueError):
    """Invalid or incomplete rule-coverage evidence."""


def _json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CoverageError(f"cannot read JSON: {path.name}") from exc
    if not isinstance(data, dict):
        raise CoverageError(f"expected JSON object: {path.name}")
    return data


def _rows(data: dict[str, Any], label: str) -> dict[str, dict[str, Any]]:
    if data.get("schema_version") != 1 or not isinstance(data.get("rules"), list):
        raise CoverageError(f"invalid {label} schema")
    rows: dict[str, dict[str, Any]] = {}
    for row in data["rules"]:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise CoverageError(f"invalid {label} rule")
        rule_id = row["id"]
        if rule_id in rows:
            raise CoverageError(f"duplicate {label} rule: {rule_id}")
        rows[rule_id] = row
    if not rows:
        raise CoverageError(f"empty {label} rule set")
    return rows


def _target_path(root: Path, value: object, rule_id: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise CoverageError(f"{rule_id}: missing relative path")
    relative = Path(value)
    if relative.is_absolute():
        raise CoverageError(f"{rule_id}: absolute paths are forbidden")
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise CoverageError(f"{rule_id}: missing or outside target path: {value}")
    return resolved


def _text(value: object, rule_id: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CoverageError(f"{rule_id}: missing {label}")
    return value


_COMMENT = re.compile(r"(^|[\s;&|()])#.*$", re.MULTILINE)
_SLICE_CONFIG = "scripts/coverage-slice.json"


def _active_text(path: Path) -> str:
    """Return file text with full-line and trailing shell/YAML comments removed."""
    return _COMMENT.sub("", path.read_text(encoding="utf-8"))


def _contract_values(root: Path, contract: dict[str, Any], rule_id: str) -> None:
    """Fail when a machine-readable registry threshold differs from the slice config."""
    values = contract.get("contract")
    threshold = values.get("threshold_percent") if isinstance(values, dict) else None
    if threshold is None:
        return
    config = _json_object(_target_path(root, _SLICE_CONFIG, rule_id))
    minimum = config.get("minimum_percent")
    if isinstance(minimum, bool) or not isinstance(minimum, (int, float)):
        raise CoverageError(f"{rule_id}: {_SLICE_CONFIG} lacks numeric minimum_percent")
    if minimum < threshold:
        raise CoverageError(
            f"{rule_id}: {_SLICE_CONFIG} minimum_percent {minimum} is below "
            f"registry threshold_percent {threshold}"
        )


def _implemented_paths(root: Path, row: dict[str, Any], rule_id: str) -> None:
    implementations = row.get("implementation_paths")
    if not isinstance(implementations, list) or not implementations:
        raise CoverageError(f"{rule_id}: no implementation paths")
    for item in implementations:
        _target_path(root, item, rule_id)

    for path_key, marker_key in (
        ("local_runner", "local_marker"),
        ("ci_runner", "ci_marker"),
    ):
        path = _target_path(root, row.get(path_key), rule_id)
        marker = _text(row.get(marker_key), rule_id, marker_key)
        if marker not in _active_text(path):
            raise CoverageError(f"{rule_id}: {path_key} lacks invocation marker")


def _configuration(root: Path, row: dict[str, Any], rule_id: str) -> None:
    evidence = row.get("configuration")
    if not isinstance(evidence, dict):
        raise CoverageError(f"{rule_id}: missing tool configuration evidence")
    path = _target_path(root, evidence.get("path"), rule_id)
    tokens = evidence.get("contains")
    if not isinstance(tokens, list) or not tokens:
        raise CoverageError(f"{rule_id}: missing configuration assertions")
    content = path.read_text(encoding="utf-8")
    for token in tokens:
        if not isinstance(token, str) or not token or token not in content:
            raise CoverageError(f"{rule_id}: configuration assertion failed")


_FIXTURE_PLACEHOLDER = "{fixture}"


def _run(args: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, check=False, timeout=timeout
    )


def _canary(root: Path, row: dict[str, Any], rule_id: str) -> None:
    canary = row.get("canary")
    if not isinstance(canary, dict):
        raise CoverageError(f"{rule_id}: missing canary")
    command = canary.get("command")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(item, str) and item for item in command)
        or not any(_FIXTURE_PLACEHOLDER in item for item in command)
    ):
        raise CoverageError(f"{rule_id}: canary command must use {{fixture}}")
    passing = _target_path(root, canary.get("passing"), rule_id)
    violating = _target_path(root, canary.get("violating"), rule_id)
    marker = _text(canary.get("diagnostic"), rule_id, "diagnostic marker")
    timeout = canary.get("timeout_seconds", 60)
    if not isinstance(timeout, int) or not 1 <= timeout <= 120:
        raise CoverageError(f"{rule_id}: invalid canary timeout")

    for fixture, should_pass in ((passing, True), (violating, False)):
        args = [part.replace(_FIXTURE_PLACEHOLDER, str(fixture)) for part in command]
        try:
            result = _run(args, root, timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CoverageError(f"{rule_id}: canary could not complete") from exc
        if should_pass and result.returncode != 0:
            raise CoverageError(f"{rule_id}: compliant canary failed")
        if not should_pass and result.returncode == 0:
            raise CoverageError(f"{rule_id}: violating canary passed")
        if not should_pass and marker not in result.stdout + result.stderr:
            raise CoverageError(f"{rule_id}: expected diagnostic missing")


def _check_rule(
    root: Path,
    contract: dict[str, Any],
    row: dict[str, Any],
    rule_id: str,
    *,
    structure_only: bool,
) -> None:
    status = row.get("status")
    if status == "not_applicable":
        if contract.get("applicability") != "conditional":
            raise CoverageError(f"{rule_id}: always-applicable rule cannot be excluded")
        _text(row.get("reason"), rule_id, "not-applicable reason")
        if row.get("user_approval") != "approved":
            raise CoverageError(f"{rule_id}: exclusion lacks user approval")
        evidence = _target_path(root, row.get("evidence_path"), rule_id)
        marker = _text(row.get("evidence_marker"), rule_id, "evidence marker")
        if marker not in evidence.read_text(encoding="utf-8"):
            raise CoverageError(f"{rule_id}: exclusion evidence marker missing")
        return
    if status != "implemented":
        raise CoverageError(f"{rule_id}: rule is not implemented")
    _implemented_paths(root, row, rule_id)
    _contract_values(root, contract, rule_id)
    if contract.get("kind") == "tool":
        _configuration(root, row, rule_id)
    if not structure_only:
        _canary(root, row, rule_id)
    elif not isinstance(row.get("canary"), dict):
        raise CoverageError(f"{rule_id}: missing planned canary")


def validate(
    root: Path,
    catalog: dict[str, Any],
    ledger: dict[str, Any],
    *,
    structure_only: bool,
) -> list[str]:
    """Return per-rule errors for structure or complete executed coverage."""
    source = _rows(catalog, "catalog")
    target = _rows(ledger, "ledger")
    errors = [f"missing rule: {item}" for item in sorted(source.keys() - target.keys())]
    errors += [f"unknown rule: {item}" for item in sorted(target.keys() - source.keys())]
    for rule_id in sorted(source.keys() & target.keys()):
        try:
            _check_rule(
                root,
                source[rule_id],
                target[rule_id],
                rule_id,
                structure_only=structure_only,
            )
        except (CoverageError, UnicodeError) as exc:
            errors.append(str(exc))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--structure-only", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if not root.is_dir():
        print("rule coverage FAIL: target root is missing", file=sys.stderr)
        return 1
    try:
        catalog = _json_object(args.catalog)
        ledger = _json_object(args.ledger)
        errors = validate(
            root,
            catalog,
            ledger,
            structure_only=args.structure_only,
        )
    except CoverageError as exc:
        errors = [str(exc)]
    if errors:
        for error in errors:
            print(f"rule coverage FAIL: {error}", file=sys.stderr)
        return 1
    exclusions = sum(
        row.get("status") == "not_applicable" for row in _rows(ledger, "ledger").values()
    )
    result = (
        "STRUCTURE_ONLY"
        if args.structure_only
        else "PASS_WITH_APPROVED_EXCLUSIONS"
        if exclusions
        else "PASS"
    )
    print(
        f"rule coverage {result}: {len(_rows(catalog, 'catalog'))} rules, {exclusions} exclusions"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

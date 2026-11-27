#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ElementTree
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from lib.proofs_settings import (
    SettingsError,
    base_text,
    changed_paths,
    exceptions,
    matches_any,
    settings_table,
    string_list,
    string_value,
    tracked_files,
    under,
)

RULE = "TEST.no-evasion"
PYTEST_MARKS = frozenset({"skip", "skipif", "xfail", "flaky"})
PYTEST_CALLS = frozenset({"skip", "xfail", "importorskip"})
UNITTEST_SKIPS = frozenset({"skip", "skipIf", "skipUnless", "expectedFailure"})
PYTEST_OPTIONS = ("--reruns", "--deselect", "-k", "--lf", "--last-failed", "--sw", "--stepwise")
BUN_OPTIONS = ("--rerun-each", "--only", "--todo", "-t", "--test-name-pattern", "--retry")
TS_MODIFIER = re.compile(r"\b(?:test|it|describe)\.(skip|skipIf|todo|todoIf|only|failing|if)\s*\(")
TS_RETRY = re.compile(r"\bretry\s*:\s*\d")
TS_TITLE = re.compile(r"\)?\s*\(\s*([\"'`])((?:(?!\1).)*)\1")
TIMEOUT_NUMBER = re.compile(r"\btimeout\s*[:=]\s*(\d+(?:\.\d+)?)")


@dataclass(frozen=True, slots=True)
class Finding:
    path: str
    line: int
    test_id: str
    problem: str

    def render(self) -> str:
        return (
            f"{self.path}:{self.line}: {RULE}: {self.test_id} {self.problem}; remove it and fix "
            "the test, or record an owner-approved proofs.toml [[exception]] whose path is the "
            "test ID"
        )


def _mark_name(target: ast.Attribute) -> str | None:
    value = target.value
    if (
        isinstance(value, ast.Attribute)
        and value.attr == "mark"
        and isinstance(value.value, ast.Name)
        and value.value.id == "pytest"
        and target.attr in PYTEST_MARKS
    ):
        return target.attr
    if isinstance(value, ast.Name) and value.id == "unittest" and target.attr in UNITTEST_SKIPS:
        return target.attr
    return None


def _marks_in(node: ast.expr) -> list[str]:
    return [
        name
        for item in ast.walk(node)
        if isinstance(item, ast.Attribute)
        for name in [_mark_name(item)]
        if name
    ]


def _test_functions(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    found: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test"
        ):
            found.append(node)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            found.extend(
                item
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and item.name.startswith("test")
            )
    return found


def _runtime_calls(function: ast.AST) -> list[tuple[int, str]]:
    calls: list[tuple[int, str]] = []
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "pytest"
            and node.func.attr in PYTEST_CALLS
        ):
            calls.append((node.lineno, node.func.attr))
    return calls


def _module_marks(tree: ast.Module) -> list[tuple[int, str]]:
    return [
        (node.lineno, mark)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "pytestmark" for target in node.targets
        )
        for mark in _marks_in(node.value)
    ]


def _function_findings(
    path: str, function: ast.FunctionDef | ast.AsyncFunctionDef, module_marks: list[tuple[int, str]]
) -> list[Finding]:
    test_id = f"{path}::{function.name}"
    findings = [
        Finding(path, line, test_id, f"is marked {mark} by the module pytestmark")
        for line, mark in module_marks
    ]
    findings.extend(
        Finding(path, decorator.lineno, test_id, f"is marked {mark}")
        for decorator in function.decorator_list
        for mark in _marks_in(decorator)
    )
    findings.extend(
        Finding(path, line, test_id, f"calls pytest.{call}")
        for line, call in _runtime_calls(function)
    )
    return findings


def python_findings(path: str, source: str) -> list[Finding]:
    tree = ast.parse(source, filename=path)
    functions = _test_functions(tree)
    module_marks = _module_marks(tree)
    findings = [
        finding
        for function in functions
        for finding in _function_findings(path, function, module_marks)
    ]
    findings.extend(
        Finding(path, line, f"{path}::{node.name}", f"calls pytest.{call}")
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node not in functions
        for line, call in _runtime_calls(node)
    )
    return findings


def typescript_findings(path: str, source: str) -> list[Finding]:
    findings: list[Finding] = []
    lines = source.splitlines()
    for number, line in enumerate(lines, start=1):
        for match in TS_MODIFIER.finditer(line):
            rest = "\n".join([line[match.end() - 1 :], *lines[number : number + 3]])
            titles = TS_TITLE.findall(rest)
            title = titles[0][1] if titles else f"line {number}"
            findings.append(Finding(path, number, f"{path}::{title}", f"uses .{match.group(1)}"))
        if TS_RETRY.search(line):
            findings.append(Finding(path, number, f"{path}:{number}", "retries until green"))
    return findings


def _pytest_option_findings(root: Path) -> list[Finding]:
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return []
    ini = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    addopts = ini.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("addopts", "")
    tokens = addopts.split() if isinstance(addopts, str) else list(addopts)
    return [
        Finding("pyproject.toml", 0, "pytest addopts", f"selects or reruns tests ({token})")
        for token in tokens
        if token.split("=")[0] in PYTEST_OPTIONS
    ]


def _script_option_findings(manifest: Path, shown: str) -> list[Finding]:
    scripts = json.loads(manifest.read_text(encoding="utf-8")).get("scripts", {})
    return [
        Finding(shown, 0, f"script {name}", f"selects or reruns tests ({token})")
        for name, command in sorted(scripts.items())
        for token in str(command).split()
        if token.split("=")[0] in BUN_OPTIONS
    ]


def config_findings(root: Path, patterns: Iterable[str]) -> list[Finding]:
    findings = _pytest_option_findings(root)
    for package in sorted({pattern.split("/")[0] for pattern in patterns}):
        manifest = root / package / "package.json"
        if manifest.is_file():
            findings.extend(_script_option_findings(manifest, f"{package}/package.json"))
    return findings


def _report_id(case: ElementTree.Element) -> str:
    name = re.sub(r"\[.*\]\Z", "", case.get("name", ""))
    file = case.get("file")
    if file:
        return f"{file}::{name}"
    classname = case.get("classname", "")
    parts = classname.split(".")
    module = [part for part in parts if not part[:1].isupper()]
    return f"{'/'.join(module)}.py::{name}"


def normalise(case: ElementTree.Element) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    for child in case:
        reason = child.get("message", "") or (child.text or "").strip()
        if child.tag == "skipped":
            outcome = "xfail" if child.get("type") == "pytest.xfail" else "skipped"
            records.append((outcome, reason))
        elif child.tag in {"rerunFailure", "rerunError", "flakyFailure", "flakyError"}:
            records.append(("rerun", reason))
    return records


def report_findings(report: Path) -> list[Finding]:
    tree = ElementTree.parse(report)
    cases = list(tree.iter("testcase"))
    if not cases:
        return [Finding(str(report), 0, "run report", "holds no test cases")]
    problems = {
        "skipped": "was skipped",
        "xfail": "was an expected failure",
        "rerun": "was retried until green",
    }
    return [
        Finding(str(report), 0, _report_id(case), f"{problems[outcome]} ({reason})")
        for case in cases
        for outcome, reason in normalise(case)
    ]


def _timeouts_by_test(path: str, source: str) -> dict[str, float]:
    found: dict[str, float] = {}
    if path.endswith(".py"):
        tree = ast.parse(source, filename=path)
        for function in _test_functions(tree):
            values = [
                float(value)
                for value in TIMEOUT_NUMBER.findall(ast.get_source_segment(source, function) or "")
            ]
            if values:
                found[function.name] = max(values)
        return found
    for number, line in enumerate(source.splitlines(), start=1):
        for value in TIMEOUT_NUMBER.findall(line):
            found[f"line {number}"] = max(found.get(f"line {number}", 0.0), float(value))
    return found


def timeout_findings(root: Path, event: str, patterns: list[str], fixtures: str) -> list[Finding]:
    findings: list[Finding] = []
    for path in changed_paths(root, event, "M"):
        if not matches_any(path, patterns) or under(path, fixtures):
            continue
        before = base_text(root, event, path)
        if before is None:
            continue
        old = _timeouts_by_test(path, before)
        new = _timeouts_by_test(path, (root / path).read_text(encoding="utf-8"))
        for name, value in sorted(new.items()):
            if name in old and value > old[name]:
                findings.append(
                    Finding(
                        path,
                        0,
                        f"{path}::{name}",
                        f"raises its timeout from {old[name]:g} to {value:g}",
                    )
                )
    return findings


def source_findings(raw: str) -> list[Finding]:
    path = Path(raw)
    if not path.is_file():
        return [Finding(raw, 0, raw, "is not a file")]
    source = path.read_text(encoding="utf-8")
    try:
        if path.suffix == ".py":
            return python_findings(raw, source)
    except SyntaxError as error:
        return [Finding(raw, error.lineno or 0, raw, f"cannot be parsed ({error.msg})")]
    if path.suffix in {".ts", ".tsx"}:
        return typescript_findings(raw, source)
    return [Finding(raw, 0, raw, "is not a Python or TypeScript test source")]


def _split(argv: list[str]) -> tuple[list[str], list[str], str | None]:
    reports: list[str] = []
    paths: list[str] = []
    event: str | None = None
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument == "--report" and index + 1 < len(argv):
            reports.append(argv[index + 1])
            index += 2
            continue
        if argument in {"--staged", "--merge-base"}:
            event = argument
        else:
            paths.append(argument)
        index += 1
    return reports, paths, event


def collect(root: Path, argv: list[str]) -> tuple[list[Finding], int]:
    table = settings_table(root, "layout")
    patterns = string_list(table, "test_file_patterns")
    fixtures = string_value(table, "fixtures_dir")
    support = string_value(table, "support_dir")
    reports, paths, event = _split(argv)
    findings: list[Finding] = []
    for report in reports:
        findings.extend(report_findings(Path(report)))
    if event is not None:
        findings.extend(timeout_findings(root, event, patterns, fixtures))
    if not reports:
        if not paths:
            listed = tracked_files(root, [*patterns, f"{support}/**/*.py", "**/conftest.py"])
            paths = [path for path in listed if not under(path, fixtures)]
            findings.extend(config_findings(root, patterns))
        for raw in paths:
            findings.extend(source_findings(raw))
    approved = exceptions(root, RULE)
    return [finding for finding in findings if finding.test_id not in approved], len(paths) + len(
        reports
    )


def main(argv: list[str]) -> int:
    try:
        findings, scanned = collect(Path.cwd(), argv)
    except (SettingsError, subprocess.CalledProcessError, OSError, ElementTree.ParseError) as error:
        print(f"{RULE}: {error}", file=sys.stderr)
        return 1
    for finding in findings:
        print(finding.render(), file=sys.stderr)
    print(f"check-test-evasion: {scanned} inputs, {len(findings)} violations", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

from __future__ import annotations

import ast
import copy
import hashlib
import json
import sys
import xml.etree.ElementTree as ElementTree
from datetime import datetime
from pathlib import Path

from lib.command_runner import isolated_environment, run_command
from lib.run_evidence import (
    CaseRecord,
    EvidenceError,
    RunReport,
    StaticCase,
    TierListing,
    listing_from_ids,
)

FRAMEWORK = "pytest"
NO_TESTS_COLLECTED = 5
_RETRY_ELEMENTS = frozenset({"rerun", "rerunFailure", "rerunError", "flakyFailure", "flakyError"})


def _identifier(case: ElementTree.Element) -> str:
    file = case.get("file", "")
    classname = case.get("classname", "")
    name = case.get("name", "")
    if not file:
        return f"{classname.replace('.', '/')}.py::{name}" if classname else name
    module = file.removesuffix(".py").replace("/", ".")
    owner = classname.removeprefix(module).lstrip(".")
    parts = [file, *(owner.split(".") if owner else []), name]
    return "::".join(parts)


def _outcome(case: ElementTree.Element) -> tuple[str, str]:
    for child in case:
        message = child.get("message", "")
        if child.tag == "skipped":
            return ("xfail" if child.get("type") == "pytest.xfail" else "skipped", message)
        if child.tag == "error":
            return "error", message
        if child.tag == "failure":
            return ("timeout" if message.startswith("Failed: Timeout") else "failed", message)
    return "passed", ""


def _record(case: ElementTree.Element) -> CaseRecord:
    outcome, reason = _outcome(case)
    reruns = sum(1 for child in case if child.tag in _RETRY_ELEMENTS)
    return CaseRecord(_identifier(case), outcome, reason, float(case.get("time", "0")), reruns)


def _started_at(suite: ElementTree.Element) -> float:
    stamp = suite.get("timestamp")
    if not stamp:
        raise EvidenceError("pytest report has no timestamp")
    return datetime.fromisoformat(stamp).timestamp()


def report(junit: Path, tier: str) -> RunReport:
    try:
        root = ElementTree.parse(junit).getroot()
    except (OSError, ElementTree.ParseError) as error:
        raise EvidenceError(f"pytest report {junit} is unreadable: {error}") from error
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    if len(suites) != 1:
        raise EvidenceError(f"pytest report {junit} must hold one test suite")
    suite = suites[0]
    cases = tuple(_record(case) for case in suite.iter("testcase"))
    return RunReport(tier, FRAMEWORK, _started_at(suite), float(suite.get("time", "0")), cases)


def parse_collection(output: str) -> list[str]:
    ids: list[str] = []
    for line in output.splitlines():
        if not line.strip():
            break
        if "::" not in line:
            raise EvidenceError(f"unexpected collect-only line: {line}")
        ids.append(line.strip())
    return ids


def collect(root: Path, tier: str, selection: list[str], timeout_seconds: float) -> TierListing:
    argv = [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"]
    completed = run_command([*argv, *selection], root, timeout_seconds, isolated_environment())
    if completed.returncode == NO_TESTS_COLLECTED:
        raise EvidenceError(f"tier {tier} collected no tests")
    if completed.returncode != 0:
        raise EvidenceError(f"tier {tier} collection failed:\n{completed.stdout}{completed.stderr}")
    return listing_from_ids(tier, FRAMEWORK, parse_collection(completed.stdout))


class _Canonical(ast.NodeTransformer):
    def __init__(self, local_names: set[str]) -> None:
        self.local_names = local_names
        self.mapping: dict[str, str] = {}

    def visit_Name(self, node: ast.Name) -> ast.Name:
        if node.id in self.local_names:
            node.id = self.mapping.setdefault(node.id, f"v{len(self.mapping)}")
        return node


def _local_names(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names = {
        node.id
        for node in ast.walk(function)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }
    names.update(
        node.name
        for node in ast.walk(function)
        if isinstance(node, ast.ExceptHandler) and node.name
    )
    return names


def _dump(node: ast.AST) -> str:
    return ast.dump(node, annotate_fields=False, include_attributes=False)


def fingerprint(function: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    clone = copy.deepcopy(function)
    body = clone.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    canonical = _Canonical(_local_names(clone))
    statements = [canonical.visit(statement) for statement in body]
    module = ast.Module(body=statements, type_ignores=[])
    calls = [_dump(node) for node in ast.walk(module) if isinstance(node, ast.Call)]
    checks = [_dump(node) for node in ast.walk(module) if isinstance(node, ast.Assert | ast.With)]
    decorators = [_dump(node) for node in clone.decorator_list]
    parameters = [argument.arg for argument in clone.args.args]
    payload = json.dumps([decorators, parameters, sorted(calls), sorted(checks)])
    return hashlib.sha256(payload.encode()).hexdigest()[:20]


def _test_functions(
    tree: ast.Module,
) -> list[tuple[tuple[str, ...], ast.FunctionDef | ast.AsyncFunctionDef]]:
    found: list[tuple[tuple[str, ...], ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
            "test"
        ):
            found.append(((node.name,), node))
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            found.extend(
                ((node.name, item.name), item)
                for item in node.body
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
                and item.name.startswith("test")
            )
    return found


def static_cases(path: str, source: str) -> list[StaticCase]:
    tree = ast.parse(source, filename=path)
    return [
        StaticCase(
            "::".join((path, *owner)), path, function.lineno, owner[-1], fingerprint(function)
        )
        for owner, function in _test_functions(tree)
    ]


def behaviour_words(name: str) -> list[str]:
    return [word for word in name.removeprefix("test").split("_") if word]

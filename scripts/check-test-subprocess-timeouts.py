#!/usr/bin/env python3
"""Fail when a test spawns a subprocess without a timeout (TEST.subprocess-timeouts)."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

from lib.proofs_settings import (
    SettingsError,
    settings_table,
    string_list,
    string_value,
    tracked_files,
    under,
)

_BLOCKING_CALLS = frozenset({"run", "call", "check_call", "check_output"})
_UNBOUNDED_OS_CALLS = frozenset({"system", "popen", "spawnl", "spawnlp", "spawnv", "spawnvp"})
_ASYNC_SPAWNS = frozenset({"create_subprocess_exec", "create_subprocess_shell"})
_TS_SPAWN = re.compile(
    r"\b(?:Bun\.spawn|Bun\.spawnSync|execSync|execFileSync|spawnSync|execFile|exec)\s*\("
)
_TS_TIMEOUT = re.compile(r"\btimeout\s*:\s*([^,}\s]+)")
RULE = "TEST.subprocess-timeouts"


def _subprocess_aliases(tree: ast.Module) -> tuple[set[str], dict[str, str]]:
    modules: set[str] = set()
    callables: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(
                alias.asname or alias.name for alias in node.names if alias.name == "subprocess"
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for alias in node.names:
                callables[alias.asname or alias.name] = alias.name
    return modules, callables


def _called_name(call: ast.Call, modules: set[str], callables: dict[str, str]) -> str | None:
    func = call.func
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id in modules
    ):
        return func.attr
    if isinstance(func, ast.Name) and func.id in callables:
        return callables[func.id]
    return None


def _module_call(call: ast.Call, module: str) -> str | None:
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return func.attr if func.value.id == module else None
    return None


def _timeout_problem(keyword: ast.keyword) -> str | None:
    value = keyword.value
    if isinstance(value, ast.Constant) and value.value is None:
        return "needs timeout="
    if isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.USub):
        return "needs a positive timeout="
    if isinstance(value, ast.Constant) and isinstance(value.value, (int, float)):
        return None if value.value > 0 else "needs a positive timeout="
    return None


def _is_timeout_context(node: ast.AsyncWith) -> bool:
    return any(
        isinstance(item.context_expr, ast.Call)
        and _module_call(item.context_expr, "asyncio") == "timeout"
        for item in node.items
    )


def _bounded_awaits(tree: ast.Module) -> set[int]:
    bounded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _module_call(node, "asyncio") == "wait_for":
            bounded.update(id(item) for item in ast.walk(node))
        elif isinstance(node, ast.AsyncWith) and _is_timeout_context(node):
            body = ast.Module(body=node.body, type_ignores=[])
            bounded.update(id(item) for item in ast.walk(body))
    return bounded


def _call_problem(node: ast.Call, name: str | None) -> str | None:
    if name in _BLOCKING_CALLS:
        timeouts = [keyword for keyword in node.keywords if keyword.arg == "timeout"]
        if not timeouts:
            return f"subprocess.{name} needs timeout="
        problem = _timeout_problem(timeouts[0])
        return f"subprocess.{name} {problem}" if problem else None
    if name == "Popen":
        return "Popen has no bound; use subprocess.run(timeout=...)"
    return None


def check_file(path: Path) -> list[str]:
    if path.suffix in {".ts", ".tsx"}:
        return check_typescript(path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError) as error:
        return [f"{path}: {RULE} unparsable source fails closed: {error}"]
    modules, callables = _subprocess_aliases(tree)
    bounded = _bounded_awaits(tree)
    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        problem = _call_problem(node, _called_name(node, modules, callables))
        if _module_call(node, "os") in _UNBOUNDED_OS_CALLS:
            problem = f"os.{_module_call(node, 'os')} has no bound; use subprocess.run(timeout=...)"
        if _module_call(node, "asyncio") in _ASYNC_SPAWNS and id(node) not in bounded:
            problem = "asyncio subprocess needs asyncio.wait_for(...) or asyncio.timeout(...)"
        if problem:
            problems.append(f"{path}:{node.lineno}: {RULE} {problem}")
    return problems


def _call_text(source: str, start: int) -> str:
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "(":
            depth += 1
        elif source[index] == ")":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    return source[start:]


def check_typescript(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    problems: list[str] = []
    for match in _TS_SPAWN.finditer(source):
        line = source.count("\n", 0, match.start()) + 1
        timeout = _TS_TIMEOUT.search(_call_text(source, match.end() - 1))
        value = timeout.group(1) if timeout else None
        if value is None:
            problems.append(f"{path}:{line}: {RULE} the process call needs a timeout: option")
        elif re.fullmatch(r"-?\d+(?:\.\d+)?", value) and float(value) <= 0:
            problems.append(f"{path}:{line}: {RULE} the process call needs a positive timeout")
    return problems


def _tracked_tests(root: Path) -> list[Path]:
    table = settings_table(root, "layout")
    fixtures = string_value(table, "fixtures_dir")
    patterns = [
        *string_list(table, "test_file_patterns"),
        *string_list(table, "support_files"),
        f"{string_value(table, 'support_dir')}/**/*.py",
        "**/conftest.py",
    ]
    return [Path(path) for path in tracked_files(root, patterns) if not under(path, fixtures)]


def main(argv: list[str]) -> int:
    try:
        paths = [Path(arg) for arg in argv] or _tracked_tests(Path.cwd())
    except (SettingsError, subprocess.CalledProcessError, OSError) as error:
        print(f"{RULE}: {error}", file=sys.stderr)
        return 1
    problems = [problem for path in paths for problem in check_file(path)]
    for problem in problems:
        print(problem, file=sys.stderr)
    print(
        f"check-test-subprocess-timeouts: {len(paths)} files, {len(problems)} violations",
        file=sys.stderr,
    )
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

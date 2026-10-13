#!/usr/bin/env python3
"""Fail when a test spawns a subprocess without a timeout (TEST.subprocess-timeouts)."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

_BLOCKING_CALLS = frozenset({"run", "call", "check_call", "check_output"})
_TEST_GLOB = "tests/*.py"


def _subprocess_aliases(tree: ast.Module) -> tuple[set[str], dict[str, str]]:
    """Return names bound to the subprocess module and to its imported callables."""
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


def _is_bound(keyword: ast.keyword) -> bool:
    """Return True for a ``timeout=`` keyword whose value is not the literal ``None``."""
    return keyword.arg == "timeout" and not (
        isinstance(keyword.value, ast.Constant) and keyword.value.value is None
    )


def check_file(path: Path) -> list[str]:
    """Return violations for subprocess calls in one test file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError) as error:
        return [f"{path}: TEST.subprocess-timeouts unparsable source fails closed: {error}"]
    modules, callables = _subprocess_aliases(tree)
    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _called_name(node, modules, callables)
        if name in _BLOCKING_CALLS and not any(_is_bound(kw) for kw in node.keywords):
            problems.append(
                f"{path}:{node.lineno}: TEST.subprocess-timeouts subprocess.{name} needs timeout="
            )
        elif name == "Popen":
            problems.append(
                f"{path}:{node.lineno}: TEST.subprocess-timeouts Popen has no bound; "
                "use subprocess.run(timeout=...)"
            )
    return problems


def _git_ls_files(glob: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "ls-files", "-z", "--", glob],
        check=True,
        stdout=subprocess.PIPE,
        timeout=30,
    )


def _tracked_tests() -> list[Path]:
    completed = _git_ls_files(_TEST_GLOB)
    paths = (Path(raw.decode()) for raw in completed.stdout.split(b"\0") if raw)
    return [path for path in paths if "fixtures" not in path.parts]


def main(argv: list[str]) -> int:
    """Check explicit files, or every tracked test module when none are given."""
    paths = [Path(arg) for arg in argv] or _tracked_tests()
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

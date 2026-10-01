#!/usr/bin/env python3
"""Fail on Python scalability patterns.

Rule IDs: PYS000 (unparsable source), PYS250 (loop depth), PYS251-PYS253
(analyzer-like code: synchronous filesystem I/O, direct subprocess or environment
access, ownership marker).
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from lib.python_quality_scope import (
    candidate_python_relpaths,
    fixture_location,
    is_maintained_python_path,
)

MAX_LOOP_DEPTH = 2
# Analyzer-like code (PYS251-PYS253) is any module under these prefixes or any module
# defining a class named ``*Analyzer``. The target currently has neither.
ANALYZER_LIKE_PREFIXES: tuple[str, ...] = ()
ANALYZER_CLASS_SUFFIX = "Analyzer"
SUBPROCESS_CALLS = frozenset({
    "run",
    "Popen",
    "call",
    "check_call",
    "check_output",
    "getoutput",
    "getstatusoutput",
})
PATH_FILESYSTEM_METHODS = frozenset({
    "exists",
    "glob",
    "is_dir",
    "is_file",
    "is_symlink",
    "iterdir",
    "lstat",
    "mkdir",
    "open",
    "read_bytes",
    "read_text",
    "rename",
    "replace",
    "rglob",
    "rmdir",
    "stat",
    "touch",
    "unlink",
    "write_bytes",
    "write_text",
})
OS_FILESYSTEM_CALLS = frozenset({
    "copy",
    "copy2",
    "copyfile",
    "copytree",
    "listdir",
    "lstat",
    "makedirs",
    "mkdir",
    "move",
    "open",
    "remove",
    "removedirs",
    "rename",
    "replace",
    "rmdir",
    "rmtree",
    "scandir",
    "stat",
    "unlink",
    "walk",
})
OS_PATH_FILESYSTEM_CALLS = frozenset({"exists", "getsize", "isdir", "isfile", "islink", "lexists"})
OWNERSHIP_MARKERS = (
    "CODEOWNERS",
    ".github/CODEOWNERS",
    "docs/CODEOWNERS",
)


@dataclass(frozen=True)
class Diagnostic:
    code: str
    relpath: str
    line: int
    message: str

    def render(self) -> str:
        return f"{self.relpath}:{self.line}: {self.code} {self.message}"


@dataclass(frozen=True)
class SourceFile:
    """A Python file to check and the repository-relative path it stands for."""

    path: Path
    relpath: str


def is_analyzer_like(relpath: str, tree: ast.AST) -> bool:
    """Return whether a module belongs to the analyzer-like scope."""
    if relpath.startswith(ANALYZER_LIKE_PREFIXES):
        return True
    return any(
        isinstance(node, ast.ClassDef) and node.name.endswith(ANALYZER_CLASS_SUFFIX)
        for node in ast.walk(tree)
    )


class ScalabilityVisitor(ast.NodeVisitor):
    def __init__(self, relpath: str, *, analyzer_like: bool) -> None:
        self.relpath = relpath
        self.analyzer_like = analyzer_like
        self.diagnostics: list[Diagnostic] = []
        self._function_stack: list[str] = []
        self._loop_depth = 0
        self._future_annotations = False
        self._subprocess_modules: set[str] = {"subprocess"}
        self._subprocess_functions: set[str] = set()

    def visit_Module(self, node: ast.Module) -> None:
        self._future_annotations = any(
            isinstance(statement, ast.ImportFrom)
            and statement.module == "__future__"
            and any(alias.name == "annotations" for alias in statement.names)
            for statement in node.body
        )
        self._collect_subprocess_imports(node)
        self.generic_visit(node)

    def _collect_subprocess_imports(self, tree: ast.Module) -> None:
        for statement in ast.walk(tree):
            if isinstance(statement, ast.Import):
                for alias in statement.names:
                    if alias.name == "subprocess":
                        self._subprocess_modules.add(alias.asname or alias.name)
            elif isinstance(statement, ast.ImportFrom) and statement.module == "subprocess":
                for alias in statement.names:
                    if alias.name in SUBPROCESS_CALLS:
                        self._subprocess_functions.add(alias.asname or alias.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_For(self, node: ast.For) -> None:
        self._visit_loop(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_loop(node)

    def visit_While(self, node: ast.While) -> None:
        self._visit_loop(node)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node.generators, (node.key, node.value))

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        previous_loop_depth = self._loop_depth
        self._loop_depth = 0
        self.visit(node.body)
        self._loop_depth = previous_loop_depth

    def visit_Call(self, node: ast.Call) -> None:
        if self.analyzer_like and self._is_sync_filesystem_call(node):
            self._report(
                "PYS251",
                node.lineno,
                "synchronous filesystem I/O in analyzer-like code",
            )
        if self.analyzer_like and self._is_direct_subprocess_call(node):
            self._report(
                "PYS252",
                node.lineno,
                "direct subprocess execution in analyzer-like code",
            )
        if self.analyzer_like and self._is_direct_env_access_call(node):
            self._report(
                "PYS252",
                node.lineno,
                "direct environment read in analyzer-like code",
            )
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if (
            self.analyzer_like
            and isinstance(node.ctx, ast.Load)
            and self._is_os_environ_subscript(node)
        ):
            self._report(
                "PYS252",
                node.lineno,
                "direct environment read in analyzer-like code",
            )
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self._check_env_assignment(node.targets, node.lineno)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._check_env_assignment([node.target], node.lineno)
        self.visit(node.target)
        if node.value is not None:
            self.visit(node.value)
        if not self._function_stack and not self._future_annotations:
            self.visit(node.annotation)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._check_env_assignment([node.target], node.lineno)
        self.generic_visit(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        if not self._future_annotations:
            annotated_arguments = (
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
                node.args.vararg,
                node.args.kwarg,
            )
            for argument in annotated_arguments:
                if argument is not None and argument.annotation is not None:
                    self.visit(argument.annotation)
            if node.returns is not None:
                self.visit(node.returns)

        self._function_stack.append(node.name)
        previous_loop_depth = self._loop_depth
        self._loop_depth = 0
        for statement in node.body:
            self.visit(statement)
        self._loop_depth = previous_loop_depth
        self._function_stack.pop()

    def _visit_loop(self, node: ast.For | ast.AsyncFor | ast.While) -> None:
        if isinstance(node, ast.For | ast.AsyncFor):
            self.visit(node.iter)
        self._enter_loop(node.lineno)
        if isinstance(node, ast.While):
            self.visit(node.test)
        else:
            self.visit(node.target)
        for statement in node.body:
            self.visit(statement)
        self._loop_depth -= 1
        for statement in node.orelse:
            self.visit(statement)

    def _visit_comprehension(
        self,
        generators: list[ast.comprehension],
        result_nodes: tuple[ast.expr, ...],
        index: int = 0,
    ) -> None:
        if index == len(generators):
            for result_node in result_nodes:
                self.visit(result_node)
            return
        generator = generators[index]
        self.visit(generator.iter)
        self._enter_loop(generator.target.lineno)
        self.visit(generator.target)
        for condition in generator.ifs:
            self.visit(condition)
        self._visit_comprehension(generators, result_nodes, index + 1)
        self._loop_depth -= 1

    def _enter_loop(self, lineno: int) -> None:
        self._loop_depth += 1
        if self._loop_depth > MAX_LOOP_DEPTH:
            self.diagnostics.append(
                Diagnostic(
                    "PYS250",
                    self.relpath,
                    lineno,
                    (
                        f"nested loop depth {self._loop_depth} exceeds "
                        f"{MAX_LOOP_DEPTH} in {self._current_function()}"
                    ),
                )
            )

    def _check_env_assignment(self, targets: list[ast.expr], lineno: int) -> None:
        if not self.analyzer_like:
            return
        if any(self._is_os_environ_subscript(target) for target in targets):
            self._report("PYS252", lineno, "direct os.environ mutation in analyzer-like code")

    def _report(self, code: str, lineno: int, message: str) -> None:
        self.diagnostics.append(
            Diagnostic(code, self.relpath, lineno, f"{message} in {self._current_function()}")
        )

    def _current_function(self) -> str:
        return self._function_stack[-1] if self._function_stack else "<module>"

    def _is_direct_subprocess_call(self, node: ast.Call) -> bool:
        func = node.func
        if isinstance(func, ast.Attribute):
            return (
                func.attr in SUBPROCESS_CALLS
                and isinstance(func.value, ast.Name)
                and func.value.id in self._subprocess_modules
            )
        return isinstance(func, ast.Name) and func.id in self._subprocess_functions

    @staticmethod
    def _is_direct_env_access_call(node: ast.Call) -> bool:
        if not isinstance(node.func, ast.Attribute):
            return False
        if (
            node.func.attr == "getenv"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os"
        ):
            return True
        return (
            node.func.attr in {"copy", "get"}
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "environ"
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == "os"
        )

    @staticmethod
    def _is_sync_filesystem_call(node: ast.Call) -> bool:
        if isinstance(node.func, ast.Name):
            return node.func.id == "open"
        if not isinstance(node.func, ast.Attribute):
            return False
        owner = node.func.value
        if isinstance(owner, ast.Name) and owner.id in {"os", "shutil"}:
            return node.func.attr in OS_FILESYSTEM_CALLS
        if (
            isinstance(owner, ast.Attribute)
            and owner.attr == "path"
            and isinstance(owner.value, ast.Name)
            and owner.value.id == "os"
        ):
            return node.func.attr in OS_PATH_FILESYSTEM_CALLS
        return node.func.attr in PATH_FILESYSTEM_METHODS

    @staticmethod
    def _is_os_environ_subscript(node: ast.expr) -> bool:
        return (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "environ"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "os"
        )


def diagnostics_for_file(source_file: SourceFile) -> tuple[list[Diagnostic], bool]:
    """Return the file's diagnostics and whether it is analyzer-like."""
    relpath = source_file.relpath
    try:
        tree = ast.parse(source_file.path.read_text(encoding="utf-8"), filename=relpath)
    except SyntaxError as exc:
        line = exc.lineno or 1
        return [
            Diagnostic("PYS000", relpath, line, f"could not parse Python file: {exc.msg}")
        ], False
    except UnicodeDecodeError as exc:
        return [Diagnostic("PYS000", relpath, 1, f"could not decode Python file: {exc}")], False
    analyzer_like = is_analyzer_like(relpath, tree)
    visitor = ScalabilityVisitor(relpath, analyzer_like=analyzer_like)
    visitor.visit(tree)
    return visitor.diagnostics, analyzer_like


def ownership_diagnostics(repo_root: Path, analyzer_relpaths: Sequence[str]) -> list[Diagnostic]:
    """Require an ownership marker once any analyzer-like code exists."""
    if not analyzer_relpaths:
        return []
    if any((repo_root / marker).is_file() for marker in OWNERSHIP_MARKERS):
        return []
    return [
        Diagnostic(
            "PYS253",
            analyzer_relpaths[0],
            1,
            f"analyzer-like code has no ownership marker ({', '.join(OWNERSHIP_MARKERS)})",
        )
    ]


def collect_diagnostics(repo_root: Path, sources: Sequence[SourceFile]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    analyzer_relpaths: list[str] = []
    for source_file in sources:
        file_diagnostics, analyzer_like = diagnostics_for_file(source_file)
        diagnostics.extend(file_diagnostics)
        if analyzer_like:
            analyzer_relpaths.append(source_file.relpath)
    diagnostics.extend(ownership_diagnostics(repo_root, analyzer_relpaths))
    return sorted(diagnostics, key=lambda item: (item.relpath, item.line, item.code))


def _selected_sources(repo_root: Path, raw_paths: Sequence[str]) -> list[SourceFile]:
    selected = [(repo_root / raw_path).resolve() for raw_path in raw_paths]
    sources: list[SourceFile] = []
    for relpath in candidate_python_relpaths(repo_root):
        if not is_maintained_python_path(relpath):
            continue
        path = (repo_root / relpath).resolve()
        if not selected or any(path == root or path.is_relative_to(root) for root in selected):
            sources.append(SourceFile(path, relpath))
    return sources


def _unmatched_paths(
    repo_root: Path, raw_paths: Sequence[str], sources: Sequence[SourceFile]
) -> list[str]:
    resolved = [source.path for source in sources]
    unmatched: list[str] = []
    for raw_path in raw_paths:
        root = (repo_root / raw_path).resolve()
        if not any(path == root or path.is_relative_to(root) for path in resolved):
            unmatched.append(raw_path)
    return unmatched


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Repository root to inspect.",
    )
    parser.add_argument(
        "--fixture",
        help=(
            "Check one canary fixture instead of the repository. Its repo-relative path is "
            "the part after the last 'repo' directory, which is also its virtual repo root."
        ),
    )
    parser.add_argument("paths", nargs="*", help="Restrict checks to these repo-relative paths.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.fixture:
        repo_root, relpath = fixture_location(args.fixture)
        sources = [SourceFile(Path(args.fixture).resolve(), relpath)]
    else:
        repo_root = args.repo_root.resolve()
        sources = _selected_sources(repo_root, args.paths)
        unmatched = _unmatched_paths(repo_root, args.paths, sources)
        if unmatched:
            print(
                "Python scalability quality check failed: paths match no maintained "
                f"Python files: {', '.join(unmatched)}",
                file=sys.stderr,
            )
            return 2
    diagnostics = collect_diagnostics(repo_root, sources)
    if diagnostics:
        print("Python scalability quality check failed:")
        for diagnostic in diagnostics:
            print(diagnostic.render())
        return 1
    print(f"Python scalability quality check passed: {len(sources)} files checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

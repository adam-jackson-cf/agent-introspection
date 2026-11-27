#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from lib.python_quality_scope import (
    SourceFile,
    is_maintained_python_path,
    path_matches_prefix,
    report_settings_error,
    select_sources,
)
from lib.python_quality_settings import (
    PROOFS_FILE,
    TABLE,
    PythonQuality,
    SettingsError,
    python_quality,
)

LABEL = "Python scalability quality check"
SETTINGS = f"{PROOFS_FILE} [{TABLE}]"
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
SYNC_FS_METHODS = frozenset({
    "chmod", "exists", "glob", "is_dir", "is_file", "is_symlink", "iterdir", "lstat", "mkdir",
    "open", "read_bytes", "read_text", "readlink", "rename", "replace", "rglob", "rmdir",
    "samefile", "stat", "symlink_to", "touch", "unlink", "walk", "write_bytes", "write_text",
})  # fmt: skip
SYNC_FS_MODULE_CALLS: dict[str, frozenset[str]] = {
    "os": frozenset({
        "access", "chmod", "listdir", "lstat", "makedirs", "mkdir", "open", "readlink", "remove",
        "removedirs", "rename", "replace", "rmdir", "scandir", "stat", "symlink", "unlink", "walk",
    }),
    "os.path": frozenset({
        "exists", "getatime", "getctime", "getmtime", "getsize", "isdir", "isfile", "islink",
        "lexists", "realpath", "samefile",
    }),
    "shutil": frozenset({"copy", "copy2", "copyfile", "copytree", "move", "rmtree"}),
    "glob": frozenset({"glob", "iglob"}),
    "io": frozenset({"open"}),
}  # fmt: skip
PATH_TYPES = frozenset({"Path", "PurePath", "PosixPath", "WindowsPath"})
PATH_RETURNING = frozenset({
    "absolute", "expanduser", "joinpath", "resolve", "with_name", "with_stem", "with_suffix",
})  # fmt: skip
PATH_ITERATING = frozenset({"glob", "iterdir", "rglob"})


@dataclass(frozen=True)
class Diagnostic:
    code: str
    relpath: str
    line: int
    message: str
    fix: str

    def render(self) -> str:
        return f"{self.relpath}:{self.line}: {self.code}: {self.message}; {self.fix}"


def is_analyzer_like(relpath: str, tree: ast.AST, settings: PythonQuality) -> bool:
    if any(path_matches_prefix(relpath, prefix) for prefix in settings.analyzer_prefixes):
        return True
    return any(
        isinstance(node, ast.ClassDef) and node.name.endswith(ANALYZER_CLASS_SUFFIX)
        for node in ast.walk(tree)
    )


def _is_path_annotation(node: ast.expr | None) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.split(".")[-1] in PATH_TYPES
    if isinstance(node, ast.Name):
        return node.id in PATH_TYPES
    return isinstance(node, ast.Attribute) and node.attr in PATH_TYPES


def is_path_expr(node: ast.expr, names: frozenset[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in names
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return is_path_expr(node.left, names)
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        return is_path_expr(node.value, names)
    return isinstance(node, ast.Call) and _is_path_call(node, names)


def _is_path_call(node: ast.Call, names: frozenset[str]) -> bool:
    func = node.func
    if _is_path_annotation(func):
        return True
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr in {"cwd", "home"} and _is_path_annotation(func.value):
        return True
    return func.attr in PATH_RETURNING and is_path_expr(func.value, names)


def _path_targets(
    node: ast.Assign | ast.For | ast.comprehension, known: frozenset[str]
) -> list[str]:
    value = node.value if isinstance(node, ast.Assign) else node.iter
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    is_path = is_path_expr(value, known) or (
        not isinstance(node, ast.Assign)
        and isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and value.func.attr in PATH_ITERATING
        and is_path_expr(value.func.value, known)
    )
    return [target.id for target in targets if is_path and isinstance(target, ast.Name)]


def _add_path_targets(tree: ast.AST, names: set[str]) -> bool:
    found = {
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign | ast.For | ast.comprehension)
        for name in _path_targets(node, frozenset(names))
    }
    added = found - names
    names |= added
    return bool(added)


def path_typed_names(tree: ast.AST) -> frozenset[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and _is_path_annotation(node.annotation):
            names.add(node.arg)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and _is_path_annotation(node.annotation)
        ):
            names.add(node.target.id)
    while _add_path_targets(tree, names):
        continue
    return frozenset(names)


def _dotted_name(node: ast.expr) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _is_os_environ(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _is_os_environ_subscript(node: ast.expr) -> bool:
    return isinstance(node, ast.Subscript) and _is_os_environ(node.value)


def _is_direct_env_access_call(node: ast.Call) -> bool:
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr == "getenv" and isinstance(func.value, ast.Name) and func.value.id == "os":
        return True
    return func.attr in {"copy", "get"} and _is_os_environ(func.value)


class ScalabilityVisitor(ast.NodeVisitor):
    def __init__(self, relpath: str, *, analyzer_like: bool, settings: PythonQuality) -> None:
        self.relpath = relpath
        self.analyzer_like = analyzer_like
        self.settings = settings
        self.diagnostics: list[Diagnostic] = []
        self._function_stack: list[str] = []
        self._loop_depth = 0
        self._future_annotations = False
        self._path_names: frozenset[str] = frozenset()
        self._subprocess_modules: set[str] = {"subprocess"}
        self._subprocess_functions: set[str] = set()

    def visit_Module(self, node: ast.Module) -> None:
        self._future_annotations = any(
            isinstance(statement, ast.ImportFrom)
            and statement.module == "__future__"
            and any(alias.name == "annotations" for alias in statement.names)
            for statement in node.body
        )
        self._path_names = path_typed_names(node)
        self._collect_subprocess_imports(node)
        self.generic_visit(node)

    def _collect_subprocess_imports(self, tree: ast.Module) -> None:
        for statement in ast.walk(tree):
            if isinstance(statement, ast.Import):
                self._subprocess_modules.update(
                    alias.asname or alias.name
                    for alias in statement.names
                    if alias.name == "subprocess"
                )
            elif isinstance(statement, ast.ImportFrom) and statement.module == "subprocess":
                self._subprocess_functions.update(
                    alias.asname or alias.name
                    for alias in statement.names
                    if alias.name in SUBPROCESS_CALLS
                )

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
        if self.analyzer_like:
            self._check_call(node)
        self.generic_visit(node)

    def _check_call(self, node: ast.Call) -> None:
        if self._is_sync_filesystem_call(node):
            self._report_outside_seam(
                "PYS251",
                node.lineno,
                "synchronous filesystem I/O",
                self.settings.filesystem_seams,
                "filesystem_seams",
            )
        if self._is_direct_subprocess_call(node):
            self._report_outside_seam(
                "PYS252",
                node.lineno,
                "direct subprocess execution",
                self.settings.subprocess_wrappers,
                "subprocess_wrappers",
            )
        if _is_direct_env_access_call(node):
            self._report_env(node.lineno, "direct environment read")

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if self.analyzer_like and isinstance(node.ctx, ast.Load) and _is_os_environ_subscript(node):
            self._report_env(node.lineno, "direct environment read")
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
        if self._loop_depth > self.settings.max_loop_depth:
            self.diagnostics.append(
                Diagnostic(
                    "PYS250",
                    self.relpath,
                    lineno,
                    f"nested loop depth {self._loop_depth} exceeds "
                    f"{self.settings.max_loop_depth} in {self._current_function()}",
                    "extract the inner loop into a function or index the data before looping",
                )
            )

    def _check_env_assignment(self, targets: list[ast.expr], lineno: int) -> None:
        if self.analyzer_like and any(_is_os_environ_subscript(target) for target in targets):
            self._report_env(lineno, "direct os.environ mutation")

    def _report_env(self, lineno: int, problem: str) -> None:
        self._report_outside_seam(
            "PYS252", lineno, problem, self.settings.environment_seams, "environment_seams"
        )

    def _report_outside_seam(
        self,
        code: str,
        lineno: int,
        problem: str,
        seams: dict[str, frozenset[str]],
        seam_key: str,
    ) -> None:
        function = self._current_function()
        if function in seams.get(self.relpath, frozenset()):
            return
        self.diagnostics.append(
            Diagnostic(
                code,
                self.relpath,
                lineno,
                f"{problem} in analyzer-like code in {function}",
                "move the call into a seam function that does only that and list it for "
                f"this file in {SETTINGS} {seam_key}",
            )
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

    def _is_sync_filesystem_call(self, node: ast.Call) -> bool:
        func = node.func
        if isinstance(func, ast.Name):
            return func.id == "open"
        if not isinstance(func, ast.Attribute):
            return False
        owner = _dotted_name(func.value)
        if owner in SYNC_FS_MODULE_CALLS:
            return func.attr in SYNC_FS_MODULE_CALLS[owner]
        return func.attr in SYNC_FS_METHODS and is_path_expr(func.value, self._path_names)


def diagnostics_for_file(
    source_file: SourceFile, settings: PythonQuality
) -> tuple[list[Diagnostic], bool]:
    relpath = source_file.relpath
    try:
        tree = ast.parse(source_file.path.read_text(encoding="utf-8"), filename=relpath)
    except SyntaxError as exc:
        return [
            Diagnostic(
                "PYS000",
                relpath,
                exc.lineno or 1,
                f"could not parse Python file: {exc.msg}",
                "fix the syntax error",
            )
        ], False
    except UnicodeDecodeError as exc:
        return [
            Diagnostic(
                "PYS000",
                relpath,
                1,
                f"could not decode Python file: {exc}",
                "save the file as UTF-8",
            )
        ], False
    analyzer_like = is_analyzer_like(relpath, tree, settings)
    visitor = ScalabilityVisitor(relpath, analyzer_like=analyzer_like, settings=settings)
    visitor.visit(tree)
    return visitor.diagnostics, analyzer_like


def ownership_diagnostics(
    root: Path, analyzer_relpaths: Sequence[str], settings: PythonQuality
) -> list[Diagnostic]:
    if not analyzer_relpaths:
        return []
    if any((root / marker).is_file() for marker in settings.ownership_markers):
        return []
    markers = ", ".join(settings.ownership_markers)
    return [
        Diagnostic(
            "PYS253",
            analyzer_relpaths[0],
            1,
            f"analyzer-like code has no ownership marker ({markers})",
            f"add one of {markers} naming the owners of the analyzer-like code",
        )
    ]


def collect_diagnostics(sources: Sequence[SourceFile]) -> list[Diagnostic]:
    settings = python_quality()
    diagnostics: list[Diagnostic] = []
    analyzer_relpaths: dict[Path, list[str]] = {}
    for source_file in sources:
        file_diagnostics, analyzer_like = diagnostics_for_file(source_file, settings)
        diagnostics.extend(file_diagnostics)
        if analyzer_like:
            analyzer_relpaths.setdefault(source_file.root, []).append(source_file.relpath)
    for root, relpaths in analyzer_relpaths.items():
        diagnostics.extend(ownership_diagnostics(root, relpaths, settings))
    return sorted(diagnostics, key=lambda item: (item.relpath, item.line, item.code))


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fail on Python scalability patterns.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Repository root to inspect.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help=(
            "Restrict checks to these paths. A path outside the repository or inside an "
            "excluded tree is a canary fixture, judged from its last 'repo' directory."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    repo_root = args.repo_root.resolve()
    try:
        selected, unmatched = select_sources(repo_root, args.paths)
        if unmatched:
            print(
                f"{LABEL} failed: paths match no maintained Python files: {', '.join(unmatched)}",
                file=sys.stderr,
            )
            return 2
        sources = [source for source in selected if is_maintained_python_path(source.relpath)]
        diagnostics = collect_diagnostics(sources)
    except SettingsError as error:
        report_settings_error(error, f"{LABEL} failed:")
        return 2
    if diagnostics:
        print(f"{LABEL} failed:")
        for diagnostic in diagnostics:
            print(diagnostic.render())
        return 1
    print(f"{LABEL} passed: {len(sources)} files checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

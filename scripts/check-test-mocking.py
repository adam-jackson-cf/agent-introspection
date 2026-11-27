#!/usr/bin/env python3
from __future__ import annotations

import ast
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from lib.proofs_settings import (
    SettingsError,
    settings_table,
    string_list,
    string_value,
    tracked_files,
    under,
)

RULE = "TEST.no-self-mocking"
PATCH_METHODS = frozenset({"setattr", "delattr", "setitem", "delitem"})
MOCK_FACTORIES = frozenset({"Mock", "MagicMock", "NonCallableMock", "AsyncMock", "create_autospec"})
MOCK_PATCHERS = frozenset({"patch", "object", "dict", "multiple"})
TS_PATTERNS = (
    re.compile(r"\bmock\.module\s*\("),
    re.compile(r"\bspyOn\s*\("),
    re.compile(r"\b(?:vi|jest)\.(?:mock|spyOn|doMock)\s*\("),
)


@dataclass(frozen=True, slots=True)
class Layout:
    root: Path
    seams: frozenset[str]
    test_patterns: tuple[str, ...]
    support_dir: str
    fixtures_dir: str


def load_layout(root: Path) -> Layout:
    table = settings_table(root, "layout")
    seams = string_list(table, "adapter_seams")
    malformed = [seam for seam in seams if seam.count(":") != 1]
    if malformed:
        raise SettingsError(
            f"proofs.toml: [layout] adapter_seams entries must read module:attribute: {malformed}"
        )
    return Layout(
        root=root,
        seams=frozenset(seam.replace(":", ".") for seam in seams),
        test_patterns=tuple(string_list(table, "test_file_patterns")),
        support_dir=string_value(table, "support_dir"),
        fixtures_dir=string_value(table, "fixtures_dir"),
    )


def seam_problems(layout: Layout) -> list[str]:
    problems: list[str] = []
    for seam in sorted(layout.seams):
        module, _, attribute = seam.rpartition(".")
        source = _module_source(layout.root, module)
        if source is None:
            problems.append(
                f"proofs.toml: {RULE}: adapter seam {module}:{attribute} names no owned module; "
                "remove it or fix the module path"
            )
        elif attribute not in _top_level_names(source):
            problems.append(
                f"proofs.toml: {RULE}: adapter seam {module}:{attribute} is not defined at the top "
                f"of {source.relative_to(layout.root).as_posix()}; remove it or fix the name"
            )
    return problems


def _module_source(root: Path, module: str) -> Path | None:
    parts = module.split(".")
    for base in (root / "src", root, root / "scripts"):
        candidate = base.joinpath(*parts)
        for path in (candidate.with_suffix(".py"), candidate / "__init__.py"):
            if path.is_file():
                return path
    return None


def _top_level_names(source: Path) -> set[str]:
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _bindings(tree: ast.Module) -> dict[str, str]:
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    bound[alias.asname] = alias.name
                else:
                    root = alias.name.split(".")[0]
                    bound[root] = root
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                bound[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return bound


def _dotted(node: ast.expr, bound: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return bound.get(node.id)
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value, bound)
        return None if base is None else f"{base}.{node.attr}"
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _literal(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


class MockingVisitor(ast.NodeVisitor):
    def __init__(self, layout: Layout, path: str, bound: dict[str, str]) -> None:
        self.layout = layout
        self.path = path
        self.bound = bound
        self.function = "<module>"
        self.problems: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        outer, self.function = self.function, node.name
        self.generic_visit(node)
        self.function = outer

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        outer, self.function = self.function, node.name
        self.generic_visit(node)
        self.function = outer

    def report(self, node: ast.AST, how: str, target: str | None) -> None:
        if target is not None and target in self.layout.seams:
            return
        line = getattr(node, "lineno", 0)
        shown = target or "an object that is not an imported module"
        owner = "owned code" if target and self.owned(target) else "a third party"
        if target is None:
            owner = "an unresolved target"
        self.problems.append(
            f"{self.path}:{line}: {RULE}: {self.function} {how} {shown} ({owner}, not a declared "
            "adapter seam); call the public entry point and substitute only a [layout] "
            "adapter_seams seam, or declare an owned adapter that wraps the third party"
        )

    def owned(self, target: str) -> bool:
        return _module_source(self.layout.root, target.split(".")[0]) is not None

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in PATCH_METHODS:
            receiver = func.value
            if isinstance(receiver, ast.Name) and receiver.id == "monkeypatch" and node.args:
                self.patch_call(node, func.attr)
        self.mock_call(node)
        self.generic_visit(node)

    def patch_call(self, node: ast.Call, method: str) -> None:
        first = node.args[0]
        if method in {"setattr", "delattr"}:
            name = _literal(node.args[1]) if len(node.args) > 1 else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                self.report(node, f"monkeypatch.{method}", first.value)
                return
            base = _dotted(first, self.bound)
            target = f"{base}.{name}" if base is not None and name is not None else None
            self.report(node, f"monkeypatch.{method}", target)
            return
        if method in {"setitem", "delitem"}:
            base = _dotted(first, self.bound)
            if base is not None and (base == "sys.modules" or self.owned(base)):
                self.report(node, f"monkeypatch.{method}", base)

    def mock_call(self, node: ast.Call) -> None:
        name = _dotted(node.func, self.bound) or ""
        leaf = name.rsplit(".", 1)[-1]
        if name.startswith("unittest.mock.") and leaf in MOCK_PATCHERS:
            target = _literal(node.args[0]) if node.args else None
            if leaf != "patch" and node.args:
                base = _dotted(node.args[0], self.bound)
                attr = _literal(node.args[1]) if len(node.args) > 1 else None
                target = f"{base}.{attr}" if base and attr else base
            self.report(node, "patches", target)
        elif name.startswith("unittest.mock.") and leaf in MOCK_FACTORIES:
            spec = node.args[0] if leaf == "create_autospec" and node.args else None
            for keyword in node.keywords:
                if keyword.arg in {"spec", "spec_set"}:
                    spec = keyword.value
            if spec is not None:
                self.report(node, f"builds {leaf} specced on", _dotted(spec, self.bound))

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self.rebinding(node, target)
        self.generic_visit(node)

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            self.rebinding(node, target)
        self.generic_visit(node)

    def rebinding(self, node: ast.AST, target: ast.expr) -> None:
        if isinstance(target, ast.Attribute):
            base = _dotted(target.value, self.bound)
            if base is not None:
                self.report(node, "rebinds", f"{base}.{target.attr}")


def check_python(layout: Layout, path: Path, shown: str) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError) as error:
        return [f"{shown}: {RULE}: cannot parse the test source: {error}; fix the syntax"]
    visitor = MockingVisitor(layout, shown, _bindings(tree))
    visitor.visit(tree)
    return visitor.problems


def check_typescript(path: Path, shown: str) -> list[str]:
    problems: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if any(pattern.search(line) for pattern in TS_PATTERNS):
            problems.append(
                f"{shown}:{number}: {RULE}: the test replaces a module or spies on owned code; "
                "pass an owned adapter interface into the code under test instead"
            )
    return problems


def default_paths(layout: Layout) -> list[str]:
    patterns = [*layout.test_patterns, f"{layout.support_dir}/**/*.py", "**/conftest.py"]
    return [
        path
        for path in tracked_files(layout.root, patterns)
        if not under(path, layout.fixtures_dir)
    ]


def check_path(layout: Layout, raw: str) -> list[str]:
    path = Path(raw)
    if not path.is_file():
        return [f"{raw}: {RULE}: no such test file; pass an existing path"]
    if path.suffix == ".py":
        return check_python(layout, path, raw)
    if path.suffix in {".ts", ".tsx"}:
        return check_typescript(path, raw)
    return [f"{raw}: {RULE}: not a Python or TypeScript test file; pass a test source"]


def main(argv: list[str]) -> int:
    root = Path.cwd()
    try:
        layout = load_layout(root)
        paths = argv or default_paths(layout)
    except (SettingsError, subprocess.CalledProcessError, OSError) as error:
        print(f"{RULE}: {error}", file=sys.stderr)
        return 1
    problems = seam_problems(layout)
    problems.extend(problem for raw in paths for problem in check_path(layout, raw))
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"check-test-mocking: {len(paths)} files, {len(problems)} violations", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

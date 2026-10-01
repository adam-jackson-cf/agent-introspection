#!/usr/bin/env python3
"""Validate Python structure, class shape, and seam boundary rules.

Rule IDs: PYQ100-107 (structure), PYQ200-203 (class shape), PYQ210-211 (analyzer
classes), PYQ220-221 (subprocess and dynamic-import seams), PYQ230-231 (contract
dataclass fields), PYQ299 (undecodable or unparsable source fails closed).
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

from lib.python_quality_scope import (
    candidate_python_relpaths,
    fixture_location,
    is_excluded_python_path,
    is_maintained_python_path,
)

CLASS_MAX_LINES = 700
CLASS_MAX_INIT_ARGS = 8
CLASS_MAX_INSTANCE_ATTRIBUTES = 12

# --- Target-owned configuration (replaces the Enaible roots and allowlists) ---

SKILLS_ROOT = ".agents/skills"
# Approved generic-name directories (PYQ103) and modules (PYQ104), as repo-relative paths.
GENERIC_NAME_ALLOWLIST: frozenset[str] = frozenset()
GENERIC_NAME_PARTS = frozenset({"common", "helpers", "misc", "stuff", "temp"})
# Files that may sit directly under tests/ (PYQ102).
ALLOWED_ROOT_FILES = frozenset({"tests/__init__.py", "tests/conftest.py"})
ALLOWED_TEST_GROUPS = frozenset({"fixtures", "integration", "seams", "support", "unit"})
# Flat tests/<glob> layout approved for a canonical group (PYQ102): group -> filename glob.
FLAT_TEST_GROUPS: dict[str, str] = {"unit": "test_*.py"}
# Command scripts directly under scripts/ are kebab-case (PYQ101); importable helper
# modules live in scripts/lib/ with snake_case names (PYQ107).
SCRIPT_MODULE_DIRECTORY = "scripts/lib"
# Directory names that are not Python package names (PYQ105).
NON_PACKAGE_DIRECTORY_PARTS = frozenset({".github", "fixtures", "scripts", "src", "tests"})
# Dedicated subprocess seam wrappers per file (PYQ220): each named function does nothing
# but run the subprocess. Files absent from this map may not call subprocess at all.
# Tests are out of scope for this rule.
SEAM_SUBPROCESS_WRAPPERS: dict[str, frozenset[str]] = {
    "scripts/check-dependency-audit.py": frozenset({"_run"}),
    "scripts/check-python-mypy.py": frozenset({"_mypy"}),
    "scripts/check-runner-failure-propagation.py": frozenset({"_run"}),
    "scripts/check-skill-metadata.py": frozenset({"_git_ls_files"}),
    "scripts/check-test-subprocess-timeouts.py": frozenset({"_git_ls_files"}),
    "scripts/guardrails-tool-canary.py": frozenset({"_run"}),
    "scripts/lib/python_quality_scope.py": frozenset({"_git_output"}),
    "scripts/validate-rule-coverage.py": frozenset({"_run"}),
    "src/agent_introspection/classify.py": frozenset({"_run_omp_token"}),
    "src/agent_introspection/drafting.py": frozenset({"run_codex"}),
    "src/agent_introspection/facts.py": frozenset({"run", "_run_password_command"}),
    "src/agent_introspection/schedule.py": frozenset({"_launchctl"}),
    "src/agent_introspection/sessions.py": frozenset({"_run_git"}),
}
# Files allowed to call importlib.import_module (PYQ221).
DYNAMIC_IMPORT_ADAPTER_ALLOWLIST: frozenset[str] = frozenset()
# Analyzer-like classes (PYQ210, PYQ211): any class named ``*Analyzer``.
ANALYZER_CLASS_SUFFIX = "Analyzer"
APPROVED_ANALYZER_BASES = frozenset({"BaseAnalyzer", "ProjectAnalyzerMixin"})
PROJECT_ANALYZER_MIXIN = "ProjectAnalyzerMixin"
PROJECT_ENTRYPOINT = "run_project_analysis"
# Dataclass collaborator contracts (PYQ230, PYQ231).
CONTRACT_PATH_PREFIXES = ("src/agent_introspection/",)


@dataclass(frozen=True)
class Diagnostic:
    """One quality-gate diagnostic."""

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


def _is_snake_case_name(name: str) -> bool:
    if name == "__init__":
        return True
    return bool(name) and all(char == "_" or char.islower() or char.isdigit() for char in name)


def _is_kebab_case_name(name: str) -> bool:
    return bool(name) and all(char == "-" or char.islower() or char.isdigit() for char in name)


def _is_valid_skill_name(name: str) -> bool:
    return bool(name) and all(char == "-" or char.islower() or char.isdigit() for char in name)


def _is_test_relpath(relpath: str) -> bool:
    return relpath.startswith("tests/")


def _generic_name_diagnostics(relpath: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    parts = Path(relpath).parts[:-1]
    for index, part in enumerate(parts):
        prefix = "/".join(parts[: index + 1])
        if part in GENERIC_NAME_PARTS and prefix not in GENERIC_NAME_ALLOWLIST:
            diagnostics.append(
                Diagnostic(
                    "PYQ103",
                    relpath,
                    1,
                    f"generic catch-all directory name '{part}' is not allowed",
                )
            )
    stem = Path(relpath).stem
    if stem in GENERIC_NAME_PARTS and relpath not in GENERIC_NAME_ALLOWLIST:
        diagnostics.append(
            Diagnostic(
                "PYQ104",
                relpath,
                1,
                f"generic catch-all module name '{stem}' is not allowed",
            )
        )
    return diagnostics


def _root_diagnostics(relpath: str) -> list[Diagnostic]:
    if is_maintained_python_path(relpath):
        return []
    return [
        Diagnostic(
            "PYQ100",
            relpath,
            1,
            "Python file is outside canonical maintained roots",
        )
    ]


def _test_group_diagnostics(relpath: str) -> list[Diagnostic]:
    if not _is_test_relpath(relpath) or relpath in ALLOWED_ROOT_FILES:
        return []
    parts = Path(relpath).parts
    if len(parts) <= 2:
        if any(fnmatchcase(parts[-1], pattern) for pattern in FLAT_TEST_GROUPS.values()):
            return []
        return [
            Diagnostic(
                "PYQ102",
                relpath,
                1,
                "test file must live under a canonical test grouping",
            )
        ]
    if parts[1] in ALLOWED_TEST_GROUPS:
        return []
    return [
        Diagnostic(
            "PYQ102",
            relpath,
            1,
            f"test path group '{parts[1]}' is not a canonical test grouping",
        )
    ]


def _package_dir_diagnostics(relpath: str) -> list[Diagnostic]:
    if relpath.startswith(f"{SKILLS_ROOT}/"):
        return []
    return [
        Diagnostic(
            "PYQ105",
            relpath,
            1,
            f"Python package directory '{part}' must use snake_case",
        )
        for part in Path(relpath).parts[:-1]
        if part not in NON_PACKAGE_DIRECTORY_PARTS and not _is_snake_case_name(part)
    ]


def _module_name_diagnostics(relpath: str) -> list[Diagnostic]:
    path = Path(relpath)
    if path.stem.startswith("test-"):
        return [
            Diagnostic(
                "PYQ107",
                relpath,
                1,
                f"test module '{path.name}' must use test_snake_case",
            )
        ]
    if _is_snake_case_name(path.stem):
        return []
    return [
        Diagnostic(
            "PYQ107",
            relpath,
            1,
            f"Python module '{path.name}' must use snake_case",
        )
    ]


def _script_name_diagnostics(relpath: str) -> list[Diagnostic]:
    path = Path(relpath)
    if path.parent.as_posix() != "scripts" or _is_kebab_case_name(path.stem):
        return []
    return [
        Diagnostic(
            "PYQ101",
            relpath,
            1,
            f"command script '{path.name}' must use kebab-case; "
            f"importable modules belong in {SCRIPT_MODULE_DIRECTORY}/",
        )
    ]


def _skill_name_diagnostics(relpath: str) -> list[Diagnostic]:
    if not relpath.startswith(f"{SKILLS_ROOT}/"):
        return []
    skill_name = Path(relpath).parts[2]
    if _is_valid_skill_name(skill_name):
        return []
    return [
        Diagnostic(
            "PYQ106",
            relpath,
            1,
            f"skill directory '{skill_name}' must use kebab-case",
        )
    ]


def structure_diagnostics_for_relpath(relpath: str) -> list[Diagnostic]:
    """Return deterministic structure diagnostics for a Python relpath."""
    normalized = relpath.removeprefix("./")
    if is_excluded_python_path(normalized):
        return []

    diagnostics = _root_diagnostics(normalized)
    if diagnostics:
        return diagnostics

    if normalized.startswith("scripts/"):
        diagnostics.extend(_script_name_diagnostics(normalized))
        if Path(normalized).parent.as_posix() != "scripts":
            diagnostics.extend(_module_name_diagnostics(normalized))
        return diagnostics + _generic_name_diagnostics(normalized)

    diagnostics.extend(_test_group_diagnostics(normalized))
    diagnostics.extend(_package_dir_diagnostics(normalized))
    diagnostics.extend(_skill_name_diagnostics(normalized))
    diagnostics.extend(_module_name_diagnostics(normalized))
    diagnostics.extend(_generic_name_diagnostics(normalized))
    return diagnostics


def skill_directory_diagnostics(repo_root: Path, already_flagged: set[str]) -> list[Diagnostic]:
    """Return PYQ106 diagnostics for skill directories with no diagnosed Python file."""
    skills = repo_root / SKILLS_ROOT
    if not skills.is_dir():
        return []
    return [
        Diagnostic(
            "PYQ106",
            f"{SKILLS_ROOT}/{child.name}",
            1,
            f"skill directory '{child.name}' must use kebab-case",
        )
        for child in sorted(skills.iterdir())
        if child.is_dir()
        and child.name not in already_flagged
        and not _is_valid_skill_name(child.name)
    ]


def _base_names(class_node: ast.ClassDef) -> set[str]:
    names: set[str] = set()
    for base in class_node.bases:
        if isinstance(base, ast.Name):
            names.add(base.id)
        elif isinstance(base, ast.Attribute):
            names.add(base.attr)
    return names


def _init_arg_count(method: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    args = list(method.args.posonlyargs) + list(method.args.args)
    if args and args[0].arg == "self":
        args = args[1:]
    return len(args) + len(method.args.kwonlyargs)


def _self_attributes(class_node: ast.ClassDef) -> set[str]:
    attributes: set[str] = set()
    pending: list[ast.AST] = list(class_node.body)
    while pending:
        node = pending.pop()
        if isinstance(node, ast.ClassDef):
            continue
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Store)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        ):
            attributes.add(node.attr)
        pending.extend(ast.iter_child_nodes(node))
    return attributes


def _function_scope_for_line(tree: ast.AST, line: int) -> str | None:
    owner: str | None = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        end_line = getattr(node, "end_lineno", node.lineno)
        if node.lineno <= line <= end_line:
            owner = node.name
    return owner


def _is_subprocess_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"run", "Popen"}
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
    )


def _is_importlib_import_module_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "import_module"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "importlib"
    )


def _annotation_name(annotation: ast.AST | None) -> str:
    if annotation is None:
        return ""
    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.Attribute):
        return annotation.attr
    if isinstance(annotation, ast.Subscript):
        return _annotation_name(annotation.value)
    if isinstance(annotation, ast.Constant):
        return str(annotation.value)
    return ""


def _annotation_subscript_children(node: ast.Subscript) -> tuple[ast.AST, ...]:
    annotation_name = _annotation_name(node.value)
    if annotation_name == "Literal":
        return (node.value,)
    if annotation_name == "Annotated" and isinstance(node.slice, ast.Tuple):
        return (node.value, node.slice.elts[0])
    return (node.value, node.slice)


def _annotation_names(annotation: ast.AST | None) -> set[str]:
    names: set[str] = set()
    pending = [annotation] if annotation is not None else []
    while pending:
        node = pending.pop()
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Subscript):
            pending.extend(_annotation_subscript_children(node))
        elif isinstance(node, ast.BinOp):
            pending.extend([node.left, node.right])
        elif isinstance(node, ast.Tuple | ast.List):
            pending.extend(node.elts)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                pending.append(ast.parse(node.value, mode="eval").body)
            except SyntaxError:
                names.add(node.value)
    return names


def _class_shape_diagnostics(class_node: ast.ClassDef, relpath: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    end_line = getattr(class_node, "end_lineno", class_node.lineno) or class_node.lineno
    line_count = end_line - class_node.lineno + 1
    if line_count > CLASS_MAX_LINES:
        diagnostics.append(
            Diagnostic(
                "PYQ200",
                relpath,
                class_node.lineno,
                f"class '{class_node.name}' has {line_count} lines > {CLASS_MAX_LINES}",
            )
        )
    attributes = _self_attributes(class_node)
    if len(attributes) > CLASS_MAX_INSTANCE_ATTRIBUTES:
        diagnostics.append(
            Diagnostic(
                "PYQ203",
                relpath,
                class_node.lineno,
                f"class '{class_node.name}' assigns {len(attributes)} instance attributes "
                f"> {CLASS_MAX_INSTANCE_ATTRIBUTES}",
            )
        )
    return diagnostics


def _constructor_diagnostics(class_node: ast.ClassDef, relpath: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for method in class_node.body:
        if not isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if method.name != "__init__":
            continue
        arg_count = _init_arg_count(method)
        if arg_count > CLASS_MAX_INIT_ARGS:
            diagnostics.append(
                Diagnostic(
                    "PYQ202",
                    relpath,
                    method.lineno,
                    f"constructor for '{class_node.name}' has {arg_count} parameters "
                    f"> {CLASS_MAX_INIT_ARGS}",
                )
            )
    return diagnostics


def _analyzer_boundary_diagnostics(class_node: ast.ClassDef, relpath: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    bases = _base_names(class_node)
    if class_node.name.endswith(ANALYZER_CLASS_SUFFIX) and not (APPROVED_ANALYZER_BASES & bases):
        diagnostics.append(
            Diagnostic(
                "PYQ210",
                relpath,
                class_node.lineno,
                f"analyzer class '{class_node.name}' must inherit "
                f"{' or '.join(sorted(APPROVED_ANALYZER_BASES))}",
            )
        )
    if PROJECT_ANALYZER_MIXIN in bases:
        defines_project_analysis = any(
            isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
            and child.name == PROJECT_ENTRYPOINT
            for child in class_node.body
        )
        if not defines_project_analysis:
            diagnostics.append(
                Diagnostic(
                    "PYQ211",
                    relpath,
                    class_node.lineno,
                    f"project analyzer class '{class_node.name}' must define {PROJECT_ENTRYPOINT}",
                )
            )
    return diagnostics


def _subprocess_boundary_diagnostics(tree: ast.AST, relpath: str) -> list[Diagnostic]:
    if _is_test_relpath(relpath):
        return []
    allowed_wrappers = SEAM_SUBPROCESS_WRAPPERS.get(relpath, frozenset())
    diagnostics: list[Diagnostic] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_subprocess_call(node)):
            continue
        owner = _function_scope_for_line(tree, node.lineno)
        if owner not in allowed_wrappers:
            diagnostics.append(
                Diagnostic(
                    "PYQ220",
                    relpath,
                    node.lineno,
                    f"subprocess.{_call_attribute(node)} must be isolated in a seam wrapper",
                )
            )
    return diagnostics


def _call_attribute(node: ast.Call) -> str:
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else ""


def _dynamic_import_boundary_diagnostics(tree: ast.AST, relpath: str) -> list[Diagnostic]:
    if relpath in DYNAMIC_IMPORT_ADAPTER_ALLOWLIST:
        return []
    return [
        Diagnostic(
            "PYQ221",
            relpath,
            node.lineno,
            "dynamic importlib.import_module calls must stay in adapter modules",
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_importlib_import_module_call(node)
    ]


def _is_dataclass_decorator(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    return _annotation_name(target) == "dataclass"


def _contract_field_diagnostics(class_node: ast.ClassDef, relpath: str) -> list[Diagnostic]:
    if not any(_is_dataclass_decorator(decorator) for decorator in class_node.decorator_list):
        return []
    diagnostics: list[Diagnostic] = []
    for node in class_node.body:
        if not isinstance(node, ast.AnnAssign):
            continue
        annotation_names = _annotation_names(node.annotation)
        if "Callable" in annotation_names:
            diagnostics.append(
                Diagnostic(
                    "PYQ230",
                    relpath,
                    node.lineno,
                    "contract dataclass fields must use named Callable aliases",
                )
            )
        if "Any" in annotation_names:
            diagnostics.append(
                Diagnostic(
                    "PYQ231",
                    relpath,
                    node.lineno,
                    "contract dataclass fields must not use Any collaborator boundaries",
                )
            )
    return diagnostics


def class_interface_diagnostics_for_source(source: str, relpath: str) -> list[Diagnostic]:
    """Return class/interface diagnostics for a source string."""
    diagnostics: list[Diagnostic] = []
    try:
        tree = ast.parse(source, filename=relpath)
    except SyntaxError as exc:
        return [
            Diagnostic(
                "PYQ299",
                relpath,
                exc.lineno or 1,
                f"could not parse Python source: {exc.msg}",
            )
        ]
    in_contract_scope = relpath.startswith(CONTRACT_PATH_PREFIXES)

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        diagnostics.extend(_class_shape_diagnostics(node, relpath))
        diagnostics.extend(_constructor_diagnostics(node, relpath))
        diagnostics.extend(_analyzer_boundary_diagnostics(node, relpath))
        if in_contract_scope:
            diagnostics.extend(_contract_field_diagnostics(node, relpath))

    diagnostics.extend(_subprocess_boundary_diagnostics(tree, relpath))
    diagnostics.extend(_dynamic_import_boundary_diagnostics(tree, relpath))
    return diagnostics


def class_interface_diagnostics(sources: Iterable[SourceFile]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for source_file in sources:
        try:
            source = source_file.path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            diagnostics.append(
                Diagnostic(
                    "PYQ299",
                    source_file.relpath,
                    1,
                    f"could not decode Python source: {exc}",
                )
            )
            continue
        diagnostics.extend(class_interface_diagnostics_for_source(source, source_file.relpath))
    return diagnostics


def _fixture_source(raw_path: str) -> SourceFile:
    """Map a canary fixture to the repo-relative path below its ``repo`` directory."""
    return SourceFile(Path(raw_path).resolve(), fixture_location(raw_path)[1])


def _selected_sources(repo_root: Path, raw_paths: Sequence[str]) -> list[SourceFile]:
    selected = [(repo_root / raw_path).resolve() for raw_path in raw_paths]
    sources: list[SourceFile] = []
    for relpath in candidate_python_relpaths(repo_root):
        path = (repo_root / relpath).resolve()
        if not selected or any(path == root or path.is_relative_to(root) for root in selected):
            sources.append(SourceFile(path, relpath))
    return sources


def _filter_checks(checks: Sequence[str]) -> set[str]:
    requested = set(checks)
    if "all" in requested:
        return {"structure", "class-interface"}
    return requested


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate maintained Python structure and interface quality."
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--check",
        action="append",
        choices=("all", "structure", "class-interface"),
        default=None,
    )
    parser.add_argument(
        "--fixture",
        action="append",
        default=[],
        help=(
            "Check one canary fixture instead of the repository. Its repo-relative path is "
            "the part after the last 'repo' directory."
        ),
    )
    parser.add_argument("paths", nargs="*", help="Restrict checks to these repo-relative paths.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    fixture_mode = bool(args.fixture)
    sources = (
        [_fixture_source(raw_path) for raw_path in args.fixture]
        if fixture_mode
        else _selected_sources(repo_root, args.paths)
    )
    checks = _filter_checks(args.check or ["all"])
    diagnostics: list[Diagnostic] = []
    if "structure" in checks:
        for source_file in sources:
            diagnostics.extend(structure_diagnostics_for_relpath(source_file.relpath))
        if not fixture_mode and not args.paths:
            flagged = {Path(item.relpath).parts[2] for item in diagnostics if item.code == "PYQ106"}
            diagnostics.extend(skill_directory_diagnostics(repo_root, flagged))
    if "class-interface" in checks:
        diagnostics.extend(
            class_interface_diagnostics(
                source_file
                for source_file in sources
                if is_maintained_python_path(source_file.relpath)
            )
        )

    if diagnostics:
        print("[python-quality] violations:", file=sys.stderr)
        for diagnostic in sorted(diagnostics, key=lambda item: item.render()):
            print(diagnostic.render(), file=sys.stderr)
        return 1
    print(f"[python-quality] OK: {len(sources)} maintained Python files checked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

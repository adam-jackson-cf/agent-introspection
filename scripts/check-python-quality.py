#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

from lib.python_quality_scope import (
    SourceFile,
    is_excluded_python_path,
    is_maintained_python_path,
    listed_relpaths,
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

LABEL = "[python-quality]"
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
SETTINGS = f"{PROOFS_FILE} [{TABLE}]"


@dataclass(frozen=True)
class Diagnostic:
    code: str
    relpath: str
    line: int
    message: str
    fix: str

    def render(self) -> str:
        return f"{self.relpath}:{self.line}: {self.code}: {self.message}; {self.fix}"


def _is_snake_case_name(name: str) -> bool:
    if name == "__init__":
        return True
    return bool(name) and all(char == "_" or char.islower() or char.isdigit() for char in name)


def _is_kebab_case_name(name: str) -> bool:
    return bool(name) and all(char == "-" or char.islower() or char.isdigit() for char in name)


def _snake_case(name: str) -> str:
    characters: list[str] = []
    for index, char in enumerate(name):
        if char.isupper() and index and name[index - 1].islower():
            characters.append("_")
        characters.append("_" if char == "-" else char.lower())
    return "".join(characters)


def _kebab_case(name: str) -> str:
    return _snake_case(name).replace("_", "-")


def _matching_root(relpath: str, roots: Iterable[str]) -> str | None:
    return next((root for root in roots if path_matches_prefix(relpath, root)), None)


def _generic_name_diagnostics(relpath: str, settings: PythonQuality) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    parts = Path(relpath).parts[:-1]
    for index, part in enumerate(parts):
        prefix = "/".join(parts[: index + 1])
        if part in settings.generic_name_parts and prefix not in settings.generic_name_allowlist:
            diagnostics.append(
                Diagnostic(
                    "PYQ103",
                    relpath,
                    1,
                    f"generic catch-all directory name '{part}' is not allowed",
                    f"rename '{prefix}' after what it owns, or add '{prefix}' to "
                    f"{SETTINGS} generic_name_allowlist after owner review",
                )
            )
    stem = Path(relpath).stem
    if stem in settings.generic_name_parts and relpath not in settings.generic_name_allowlist:
        diagnostics.append(
            Diagnostic(
                "PYQ104",
                relpath,
                1,
                f"generic catch-all module name '{stem}' is not allowed",
                f"rename the module after what it owns, or add '{relpath}' to "
                f"{SETTINGS} generic_name_allowlist after owner review",
            )
        )
    return diagnostics


def _root_diagnostics(relpath: str, settings: PythonQuality) -> list[Diagnostic]:
    if is_maintained_python_path(relpath):
        return []
    roots = ", ".join(settings.maintained_roots)
    return [
        Diagnostic(
            "PYQ100",
            relpath,
            1,
            "Python file is outside canonical maintained roots",
            f"move it under one of {roots}, or add its root to {SETTINGS} "
            "maintained_roots or excluded_paths with a reason",
        )
    ]


def _test_group_diagnostics(relpath: str, settings: PythonQuality) -> list[Diagnostic]:
    if not path_matches_prefix(relpath, settings.test_root):
        return []
    if relpath in settings.test_root_files:
        return []
    parts = Path(relpath).relative_to(settings.test_root).parts
    groups = ", ".join(sorted(settings.test_groups))
    if len(parts) <= 1:
        if any(fnmatchcase(parts[-1], pattern) for pattern in settings.flat_test_groups.values()):
            return []
        flat = ", ".join(sorted(settings.flat_test_groups.values()))
        return [
            Diagnostic(
                "PYQ102",
                relpath,
                1,
                "test file must live under a canonical test grouping",
                f"name it to match {flat} or move it under one of {groups}",
            )
        ]
    if parts[0] in settings.test_groups:
        return []
    return [
        Diagnostic(
            "PYQ102",
            relpath,
            1,
            f"test path group '{parts[0]}' is not a canonical test grouping",
            f"move it under one of {groups}",
        )
    ]


def _package_dir_diagnostics(relpath: str, settings: PythonQuality) -> list[Diagnostic]:
    if _matching_root(relpath, settings.skill_roots) is not None:
        return []
    return [
        Diagnostic(
            "PYQ105",
            relpath,
            1,
            f"Python package directory '{part}' must use snake_case",
            f"rename it to '{_snake_case(part)}'",
        )
        for part in Path(relpath).parts[:-1]
        if part not in settings.non_package_directories and not _is_snake_case_name(part)
    ]


def _module_name_diagnostics(relpath: str) -> list[Diagnostic]:
    path = Path(relpath)
    if _is_snake_case_name(path.stem):
        return []
    return [
        Diagnostic(
            "PYQ107",
            relpath,
            1,
            f"Python module '{path.name}' must use snake_case",
            f"rename it to '{_snake_case(path.stem)}.py'",
        )
    ]


def _script_name_diagnostics(relpath: str, settings: PythonQuality) -> list[Diagnostic]:
    path = Path(relpath)
    if path.parent.as_posix() != settings.script_root:
        return []
    if not _is_kebab_case_name(path.stem):
        return [
            Diagnostic(
                "PYQ101",
                relpath,
                1,
                f"command script '{path.name}' must use kebab-case",
                f"rename it to '{_kebab_case(path.stem)}.py', or move an importable module "
                f"into {settings.script_module_directory}/",
            )
        ]
    if path.name in settings.script_names:
        return []
    return [
        Diagnostic(
            "PYQ101",
            relpath,
            1,
            f"command script '{path.name}' is not in the approved script names",
            f"rename it to an approved script, or add '{path.name}' to {SETTINGS} "
            "script_names after owner review",
        )
    ]


def _skill_name(relpath: str, settings: PythonQuality) -> str | None:
    root = _matching_root(relpath, settings.skill_roots)
    if root is None:
        return None
    parts = Path(relpath).relative_to(root).parts
    return parts[0] if len(parts) > 1 else None


def _skill_name_diagnostic(location: str, skill_name: str) -> Diagnostic:
    return Diagnostic(
        "PYQ106",
        location,
        1,
        f"skill directory '{skill_name}' must use kebab-case",
        f"rename it to '{_kebab_case(skill_name)}'",
    )


def _skill_name_diagnostics(relpath: str, settings: PythonQuality) -> list[Diagnostic]:
    skill_name = _skill_name(relpath, settings)
    if skill_name is None or _is_kebab_case_name(skill_name):
        return []
    return [_skill_name_diagnostic(relpath, skill_name)]


def structure_diagnostics_for_relpath(relpath: str, settings: PythonQuality) -> list[Diagnostic]:
    normalized = relpath.removeprefix("./")
    if is_excluded_python_path(normalized):
        return []

    diagnostics = _root_diagnostics(normalized, settings)
    if diagnostics:
        return diagnostics

    if path_matches_prefix(normalized, settings.script_root):
        diagnostics.extend(_script_name_diagnostics(normalized, settings))
        if Path(normalized).parent.as_posix() != settings.script_root:
            diagnostics.extend(_module_name_diagnostics(normalized))
        return diagnostics + _generic_name_diagnostics(normalized, settings)

    diagnostics.extend(_test_group_diagnostics(normalized, settings))
    diagnostics.extend(_package_dir_diagnostics(normalized, settings))
    diagnostics.extend(_skill_name_diagnostics(normalized, settings))
    if not path_matches_prefix(normalized, settings.test_root):
        diagnostics.extend(_module_name_diagnostics(normalized))
    diagnostics.extend(_generic_name_diagnostics(normalized, settings))
    return diagnostics


def skill_directory_diagnostics(
    repo_root: Path, settings: PythonQuality, already_flagged: set[str]
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for root in settings.skill_roots:
        names = {
            Path(relpath).relative_to(root).parts[0]
            for relpath in listed_relpaths(repo_root, [root])
            if len(Path(relpath).relative_to(root).parts) > 1
        }
        diagnostics.extend(
            _skill_name_diagnostic(f"{root}/{name}", name)
            for name in sorted(names)
            if name not in already_flagged and not _is_kebab_case_name(name)
        )
    return diagnostics


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


def _subprocess_import_names(tree: ast.AST) -> tuple[set[str], set[str]]:
    module_names = {"subprocess"}
    function_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            module_names.update(
                alias.asname or alias.name for alias in node.names if alias.name == "subprocess"
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            function_names.update(
                alias.asname or alias.name for alias in node.names if alias.name in SUBPROCESS_CALLS
            )
    return module_names, function_names


def _is_subprocess_call(node: ast.Call, module_names: set[str], function_names: set[str]) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute):
        return (
            func.attr in SUBPROCESS_CALLS
            and isinstance(func.value, ast.Name)
            and func.value.id in module_names
        )
    return isinstance(func, ast.Name) and func.id in function_names


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


def _class_shape_diagnostics(
    class_node: ast.ClassDef, relpath: str, settings: PythonQuality
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    end_line = getattr(class_node, "end_lineno", class_node.lineno) or class_node.lineno
    line_count = end_line - class_node.lineno + 1
    if line_count > settings.class_max_lines:
        diagnostics.append(
            Diagnostic(
                "PYQ200",
                relpath,
                class_node.lineno,
                f"class '{class_node.name}' has {line_count} lines > {settings.class_max_lines}",
                "split the class into collaborators with one responsibility each",
            )
        )
    attributes = _self_attributes(class_node)
    if len(attributes) > settings.class_max_instance_attributes:
        diagnostics.append(
            Diagnostic(
                "PYQ203",
                relpath,
                class_node.lineno,
                f"class '{class_node.name}' assigns {len(attributes)} instance attributes "
                f"> {settings.class_max_instance_attributes}",
                "group related attributes into a value object or split the class",
            )
        )
    return diagnostics


def _constructor_diagnostics(
    class_node: ast.ClassDef, relpath: str, settings: PythonQuality
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for method in class_node.body:
        if not isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if method.name != "__init__":
            continue
        arg_count = _init_arg_count(method)
        if arg_count > settings.class_max_init_args:
            diagnostics.append(
                Diagnostic(
                    "PYQ202",
                    relpath,
                    method.lineno,
                    f"constructor for '{class_node.name}' has {arg_count} parameters "
                    f"> {settings.class_max_init_args}",
                    "pass a parameter object or split the class",
                )
            )
    return diagnostics


def _analyzer_boundary_diagnostics(
    class_node: ast.ClassDef, relpath: str, settings: PythonQuality
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    bases = _base_names(class_node)
    if class_node.name.endswith(ANALYZER_CLASS_SUFFIX) and not (settings.analyzer_bases & bases):
        approved = " or ".join(sorted(settings.analyzer_bases)) or "an approved base"
        diagnostics.append(
            Diagnostic(
                "PYQ210",
                relpath,
                class_node.lineno,
                f"analyzer class '{class_node.name}' must inherit {approved}",
                f"inherit {approved} listed in {SETTINGS} analyzer_bases, "
                f"or rename the class without the '{ANALYZER_CLASS_SUFFIX}' suffix",
            )
        )
    for base, entrypoint in sorted(settings.project_analyzer_entrypoints.items()):
        if base not in bases:
            continue
        defines_entrypoint = any(
            isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) and child.name == entrypoint
            for child in class_node.body
        )
        if not defines_entrypoint:
            diagnostics.append(
                Diagnostic(
                    "PYQ211",
                    relpath,
                    class_node.lineno,
                    f"project analyzer class '{class_node.name}' must define {entrypoint}",
                    f"define {entrypoint} on '{class_node.name}'",
                )
            )
    return diagnostics


def _call_attribute(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _subprocess_boundary_diagnostics(
    tree: ast.AST, relpath: str, settings: PythonQuality
) -> list[Diagnostic]:
    allowed_wrappers = settings.subprocess_wrappers.get(relpath, frozenset())
    diagnostics: list[Diagnostic] = []
    module_names, function_names = _subprocess_import_names(tree)
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call) and _is_subprocess_call(node, module_names, function_names)
        ):
            continue
        owner = _function_scope_for_line(tree, node.lineno)
        if owner not in allowed_wrappers:
            diagnostics.append(
                Diagnostic(
                    "PYQ220",
                    relpath,
                    node.lineno,
                    f"subprocess.{_call_attribute(node)} in {owner or '<module>'} is outside "
                    "an approved seam wrapper",
                    "call it from a function that only runs the subprocess and list that "
                    f"function for this file in {SETTINGS} subprocess_wrappers",
                )
            )
    return diagnostics


def _dynamic_import_boundary_diagnostics(
    tree: ast.AST, relpath: str, settings: PythonQuality
) -> list[Diagnostic]:
    if relpath in settings.dynamic_import_adapters:
        return []
    return [
        Diagnostic(
            "PYQ221",
            relpath,
            node.lineno,
            "dynamic importlib.import_module call is outside an adapter module",
            "import the module statically, or move the call into an adapter module listed "
            f"in {SETTINGS} dynamic_import_adapters",
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
                    "contract dataclass field uses a bare Callable",
                    "declare a named type alias for the callable and annotate the field with it",
                )
            )
        if "Any" in annotation_names:
            diagnostics.append(
                Diagnostic(
                    "PYQ231",
                    relpath,
                    node.lineno,
                    "contract dataclass field uses Any as a collaborator boundary",
                    "annotate the field with the collaborator's concrete type or a Protocol",
                )
            )
    return diagnostics


def class_interface_diagnostics_for_source(
    source: str, relpath: str, settings: PythonQuality
) -> list[Diagnostic]:
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
                "fix the syntax error",
            )
        ]
    in_contract_scope = _matching_root(relpath, settings.contract_roots) is not None

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        diagnostics.extend(_class_shape_diagnostics(node, relpath, settings))
        diagnostics.extend(_constructor_diagnostics(node, relpath, settings))
        diagnostics.extend(_analyzer_boundary_diagnostics(node, relpath, settings))
        if in_contract_scope:
            diagnostics.extend(_contract_field_diagnostics(node, relpath))

    diagnostics.extend(_subprocess_boundary_diagnostics(tree, relpath, settings))
    diagnostics.extend(_dynamic_import_boundary_diagnostics(tree, relpath, settings))
    return diagnostics


def class_interface_diagnostics(
    sources: Iterable[SourceFile], settings: PythonQuality
) -> list[Diagnostic]:
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
                    "save the file as UTF-8",
                )
            )
            continue
        diagnostics.extend(
            class_interface_diagnostics_for_source(source, source_file.relpath, settings)
        )
    return diagnostics


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
        "paths",
        nargs="*",
        help=(
            "Restrict checks to these paths. A path outside the repository or inside an "
            "excluded tree is a canary fixture, judged from its last 'repo' directory."
        ),
    )
    return parser


def collect_diagnostics(
    repo_root: Path, sources: Sequence[SourceFile], checks: set[str], *, whole_repo: bool
) -> list[Diagnostic]:
    settings = python_quality()
    diagnostics: list[Diagnostic] = []
    if "structure" in checks:
        for source_file in sources:
            diagnostics.extend(structure_diagnostics_for_relpath(source_file.relpath, settings))
        if whole_repo:
            flagged = {
                name
                for item in diagnostics
                if item.code == "PYQ106"
                and (name := _skill_name(item.relpath, settings)) is not None
            }
            diagnostics.extend(skill_directory_diagnostics(repo_root, settings, flagged))
    if "class-interface" in checks:
        diagnostics.extend(
            class_interface_diagnostics(
                (source for source in sources if is_maintained_python_path(source.relpath)),
                settings,
            )
        )
    return diagnostics


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    try:
        sources, unmatched = select_sources(repo_root, args.paths)
        if unmatched:
            print(f"{LABEL} paths match no Python files: {', '.join(unmatched)}", file=sys.stderr)
            return 2
        diagnostics = collect_diagnostics(
            repo_root, sources, _filter_checks(args.check or ["all"]), whole_repo=not args.paths
        )
    except SettingsError as error:
        report_settings_error(error, LABEL)
        return 2

    if diagnostics:
        print(f"{LABEL} violations:", file=sys.stderr)
        for diagnostic in sorted(diagnostics, key=lambda item: item.render()):
            print(diagnostic.render(), file=sys.stderr)
        return 1
    print(f"{LABEL} OK: {len(sources)} maintained Python files checked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

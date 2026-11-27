#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

from lib.python_quality_scope import (
    SourceFile,
    candidate_python_relpaths,
    is_excluded_python_path,
    is_maintained_python_path,
    path_matches_prefix,
    report_settings_error,
    select_sources,
)
from lib.python_quality_settings import PythonQuality, SettingsError, python_quality

LABEL = "[fanout]"
GENERAL_FIX = "split the module or route the imports through an owned adapter module"
LARGE_MODULE_FIX = "split the large module so each part needs fewer architectural imports"


@dataclass(frozen=True)
class SourceRoot:
    path: Path
    relpath: str


@dataclass(frozen=True)
class PythonFile:
    path: Path
    relpath: str
    module: str | None
    is_package: bool


@dataclass(frozen=True)
class FanoutLimits:
    architectural_max: int
    large_module_nloc: int
    large_module_architectural_max: int


@dataclass(frozen=True)
class FanoutResult:
    relpath: str
    count: int
    limit: int
    nloc: int
    dependencies: tuple[str, ...]
    reasons: tuple[str, ...]
    line: int = 1


def _canonical_relpath(repo_root: Path, path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(repo_root).as_posix()
    except ValueError as exc:
        raise SystemExit(f"path is outside repo root: {path}") from exc


def _normalise_source_roots(repo_root: Path, values: list[str]) -> tuple[SourceRoot, ...]:
    roots: list[SourceRoot] = []
    for value in values:
        root_path = (repo_root / value).resolve()
        if not root_path.is_dir():
            continue
        relpath = _canonical_relpath(repo_root, root_path)
        if is_excluded_python_path(f"{relpath}/"):
            raise SystemExit(f"source root is excluded: {value}")
        roots.append(SourceRoot(path=root_path, relpath=relpath))
    return tuple(sorted(roots, key=lambda item: len(item.path.parts), reverse=True))


def discover_python_files(
    repo_root: Path,
    *,
    source_roots: tuple[SourceRoot, ...],
) -> list[Path]:
    discovered: list[Path] = []
    for relpath in candidate_python_relpaths(repo_root):
        path = repo_root / relpath
        in_roots = any(
            root.relpath == "." or relpath.startswith(f"{root.relpath}/") for root in source_roots
        )
        if in_roots and is_maintained_python_path(relpath) and not path.is_symlink():
            discovered.append(path)
    return discovered


def _module_for_path(path: Path, source_roots: tuple[SourceRoot, ...]) -> tuple[str | None, bool]:
    for source_root in source_roots:
        try:
            rel = path.resolve().relative_to(source_root.path)
        except ValueError:
            continue
        if rel.name == "__init__.py":
            parts = rel.parts[:-1]
            return (".".join(parts) if parts else None), True
        module_parts = (*rel.parts[:-1], rel.stem)
        return ".".join(module_parts), False
    return None, False


def collect_python_files(
    repo_root: Path,
    *,
    source_roots: tuple[SourceRoot, ...],
) -> list[PythonFile]:
    files: list[PythonFile] = []
    for path in discover_python_files(repo_root, source_roots=source_roots):
        module, is_package = _module_for_path(path, source_roots)
        files.append(
            PythonFile(
                path=path,
                relpath=path.relative_to(repo_root).as_posix(),
                module=module,
                is_package=is_package,
            )
        )
    return files


def _resolve_relative_import(current: PythonFile, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module
    if current.module is None:
        return None
    package_parts = (
        current.module.split(".") if current.is_package else current.module.split(".")[:-1]
    )
    if node.level > 1:
        package_parts = package_parts[: -(node.level - 1)]
    if node.module:
        package_parts.extend(node.module.split("."))
    return ".".join(package_parts)


def _normalise_known_module(
    module: str,
    *,
    root_packages: set[str],
    known_modules: set[str],
) -> str:
    root = module.split(".", 1)[0]
    if root not in root_packages:
        return module
    parts = module.split(".")
    for end in range(len(parts), 0, -1):
        candidate = ".".join(parts[:end])
        if candidate in known_modules:
            return candidate
    return root


def imported_modules(
    source: str,
    current: PythonFile,
    *,
    root_packages: set[str],
    known_modules: set[str],
) -> set[str]:
    tree = ast.parse(source, filename=current.relpath)
    imports: set[str] = set()
    for node in ast.walk(tree):
        imports.update(
            _imports_from_node(
                node,
                current,
                root_packages=root_packages,
                known_modules=known_modules,
            )
        )
    if current.module is not None:
        imports.discard(current.module)
    return imports


def _imports_from_node(
    node: ast.AST,
    current: PythonFile,
    *,
    root_packages: set[str],
    known_modules: set[str],
) -> set[str]:
    dynamic_import = _literal_importlib_import_module(node)
    if dynamic_import is not None:
        return {
            _normalise_known_module(
                dynamic_import,
                root_packages=root_packages,
                known_modules=known_modules,
            )
        }
    if isinstance(node, ast.Import):
        return {
            _normalise_known_module(
                alias.name,
                root_packages=root_packages,
                known_modules=known_modules,
            )
            for alias in node.names
        }
    if isinstance(node, ast.ImportFrom):
        return _imports_from_import_from(
            node,
            current,
            root_packages=root_packages,
            known_modules=known_modules,
        )
    return set()


def _imports_from_import_from(
    node: ast.ImportFrom,
    current: PythonFile,
    *,
    root_packages: set[str],
    known_modules: set[str],
) -> set[str]:
    module = _resolve_relative_import(current, node)
    if module is None:
        return set()
    root = module.split(".", 1)[0]
    if root not in root_packages:
        return {module}

    imports: set[str] = set()
    for alias in node.names:
        candidate = module if alias.name == "*" else f"{module}.{alias.name}"
        dependency = _normalise_known_module(
            candidate,
            root_packages=root_packages,
            known_modules=known_modules,
        )
        if candidate not in known_modules:
            dependency = _normalise_known_module(
                module,
                root_packages=root_packages,
                known_modules=known_modules,
            )
        imports.add(dependency)
    return imports


def _literal_importlib_import_module(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    if not isinstance(node.func, ast.Attribute) or node.func.attr != "import_module":
        return None
    if not isinstance(node.func.value, ast.Name) or node.func.value.id != "importlib":
        return None
    module_name = _literal_string_argument(node, position=0, keyword="name")
    if module_name is None or not module_name.startswith("."):
        return module_name
    package = _literal_string_argument(node, position=1, keyword="package")
    if package is None:
        return None
    return _resolve_relative_dynamic_import(module_name, package)


def _literal_string_argument(node: ast.Call, *, position: int, keyword: str) -> str | None:
    argument = (
        node.args[position]
        if len(node.args) > position
        else next(
            (argument.value for argument in node.keywords if argument.arg == keyword),
            None,
        )
    )
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        return argument.value
    return None


def _resolve_relative_dynamic_import(module_name: str, package: str) -> str | None:
    if not package:
        return None
    level = len(module_name) - len(module_name.lstrip("."))
    package_parts = package.split(".")
    if level > len(package_parts):
        return None
    base = package_parts[: len(package_parts) - level + 1]
    suffix = module_name[level:]
    return ".".join((*base, suffix)) if suffix else ".".join(base)


def _is_stdlib_module(module: str) -> bool:
    root = module.split(".", 1)[0]
    return root == "__future__" or root in sys.stdlib_module_names


def architectural_dependencies(imports: set[str]) -> set[str]:
    return {module for module in imports if not _is_stdlib_module(module)}


def source_nloc(source: str) -> int:
    return sum(
        1 for line in source.splitlines() if line.strip() and not line.lstrip().startswith("#")
    )


def _fanout_reasons(count: int, nloc: int, limits: FanoutLimits) -> tuple[str, ...]:
    reasons: list[str] = []
    if count > limits.architectural_max:
        reasons.append(
            f"FANOUT.general: architectural imports {count} > {limits.architectural_max}"
        )
    if nloc > limits.large_module_nloc and count > limits.large_module_architectural_max:
        reasons.append(
            f"FANOUT.large-module: large module {nloc} NLOC with architectural imports "
            f"{count} > {limits.large_module_architectural_max}"
        )
    return tuple(reasons)


def _reason_fix(reason: str) -> str:
    return LARGE_MODULE_FIX if reason.startswith("FANOUT.large-module") else GENERAL_FIX


def _unreadable_result(python_file: PythonFile, line: int, problem: str, fix: str) -> FanoutResult:
    return FanoutResult(
        relpath=python_file.relpath,
        count=0,
        limit=0,
        nloc=0,
        dependencies=(),
        reasons=(f"FANOUT.general: {problem}; {fix}",),
        line=line,
    )


def analyse_fanout(
    files: list[PythonFile],
    *,
    root_packages: set[str],
    limits: FanoutLimits,
) -> list[FanoutResult]:
    known_modules = {python_file.module for python_file in files if python_file.module}
    for module in tuple(known_modules):
        parts = module.split(".")
        known_modules.update(".".join(parts[:parent_end]) for parent_end in range(1, len(parts)))
    known_modules.update(root_packages)
    results: list[FanoutResult] = []
    for python_file in files:
        try:
            source = python_file.path.read_text(encoding="utf-8")
            imports = imported_modules(
                source,
                python_file,
                root_packages=root_packages,
                known_modules=known_modules,
            )
        except UnicodeDecodeError as exc:
            results.append(
                _unreadable_result(
                    python_file, 1, f"could not decode Python file: {exc}", "save the file as UTF-8"
                )
            )
            continue
        except SyntaxError as exc:
            results.append(
                _unreadable_result(
                    python_file,
                    exc.lineno or 1,
                    f"could not parse Python file: {exc.msg}",
                    "fix the syntax error",
                )
            )
            continue
        dependencies = architectural_dependencies(imports)
        nloc = source_nloc(source)
        results.append(
            FanoutResult(
                relpath=python_file.relpath,
                count=len(dependencies),
                limit=limits.architectural_max,
                nloc=nloc,
                dependencies=tuple(sorted(dependencies)),
                reasons=_fanout_reasons(len(dependencies), nloc, limits),
            )
        )
    return results


def _limits(settings: PythonQuality) -> FanoutLimits:
    return FanoutLimits(
        architectural_max=settings.fanout_max,
        large_module_nloc=settings.large_module_nloc,
        large_module_architectural_max=settings.large_module_fanout_max,
    )


def _fixture_file(source: SourceFile) -> PythonFile:
    return PythonFile(
        path=source.path, relpath=source.relpath, module=source.path.stem, is_package=False
    )


def _repo_files(repo_root: Path, settings: PythonQuality) -> list[PythonFile]:
    source_roots = _normalise_source_roots(
        repo_root, [*settings.maintained_roots, *settings.skill_roots]
    )
    return collect_python_files(repo_root, source_roots=source_roots)


def _in_selection(relpath: str, selected: set[str]) -> bool:
    return any(path_matches_prefix(relpath, item) for item in selected)


def _results(repo_root: Path, raw_paths: list[str]) -> tuple[list[FanoutResult], list[str]]:
    settings = python_quality()
    limits = _limits(settings)
    root_packages = set(settings.fanout_root_packages)
    selected, unmatched = select_sources(repo_root, raw_paths)
    fixtures = [_fixture_file(source) for source in selected if source.root != repo_root]
    results = analyse_fanout(fixtures, root_packages=root_packages, limits=limits)
    repo_relpaths = {source.relpath for source in selected if source.root == repo_root}
    if repo_relpaths or not raw_paths:
        repo_results = analyse_fanout(
            _repo_files(repo_root, settings), root_packages=root_packages, limits=limits
        )
        results.extend(
            result
            for result in repo_results
            if not raw_paths or _in_selection(result.relpath, repo_relpaths)
        )
    return results, unmatched


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail when Python modules have broad architectural fan-out."
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "paths",
        nargs="*",
        help=(
            "Report only these paths. A path outside the repository or inside an excluded "
            "tree is a canary fixture, judged from its last 'repo' directory."
        ),
    )
    args = parser.parse_args(argv)
    try:
        results, unmatched = _results(args.repo_root.resolve(), args.paths)
    except SettingsError as error:
        report_settings_error(error, LABEL)
        return 2
    if unmatched:
        print(f"{LABEL} paths match no Python files: {', '.join(unmatched)}", file=sys.stderr)
        return 2

    failures = [result for result in results if result.reasons]
    if not failures:
        print(f"{LABEL} OK: {len(results)} Python files checked")
        return 0

    print(f"{LABEL} Architectural fan-out limit exceeded:", file=sys.stderr)
    for result in sorted(failures, key=lambda item: (-item.count, item.relpath)):
        dependencies = ", ".join(result.dependencies)
        for reason in result.reasons:
            line = f"{result.relpath}:{result.line}: {reason}"
            if result.dependencies:
                line = f"{line} ({dependencies}); {_reason_fix(reason)}"
            print(line, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

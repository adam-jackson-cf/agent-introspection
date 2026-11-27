from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import cast

from lib import filesystem

PROOFS_FILE = "proofs.toml"
TABLE = "python_quality"
OWNING_REPO = Path(__file__).resolve().parents[2]

type Table = dict[str, object]


class SettingsError(Exception):
    pass


@dataclass(frozen=True)
class PythonQuality:
    maintained_roots: tuple[str, ...]
    skill_roots: tuple[str, ...]
    excluded_paths: dict[str, str]
    script_root: str
    script_module_directory: str
    script_names: frozenset[str]
    test_root: str
    test_root_files: frozenset[str]
    test_groups: frozenset[str]
    flat_test_groups: dict[str, str]
    non_package_directories: frozenset[str]
    generic_name_parts: frozenset[str]
    generic_name_allowlist: frozenset[str]
    class_max_lines: int
    class_max_init_args: int
    class_max_instance_attributes: int
    analyzer_bases: frozenset[str]
    project_analyzer_entrypoints: dict[str, str]
    subprocess_wrappers: dict[str, frozenset[str]]
    dynamic_import_adapters: frozenset[str]
    contract_roots: tuple[str, ...]
    max_loop_depth: int
    analyzer_prefixes: tuple[str, ...]
    ownership_markers: tuple[str, ...]
    filesystem_seams: dict[str, frozenset[str]]
    environment_seams: dict[str, frozenset[str]]
    fanout_root_packages: frozenset[str]
    fanout_max: int
    large_module_nloc: int
    large_module_fanout_max: int


def _fail(key: str, problem: str) -> SettingsError:
    return SettingsError(
        f"{PROOFS_FILE}: [{TABLE}] {key} {problem}; set it in the [{TABLE}] table of {PROOFS_FILE}"
    )


def _strings(table: Table, key: str) -> tuple[str, ...]:
    value = table[key]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _fail(key, "must be a list of strings")
    return tuple(cast("list[str]", value))


def _string(table: Table, key: str) -> str:
    value = table[key]
    if not isinstance(value, str) or not value:
        raise _fail(key, "must be a nonempty string")
    return value


def _limit(table: Table, key: str) -> int:
    value = table[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _fail(key, "must be a positive integer")
    return value


def _string_map(table: Table, key: str) -> dict[str, str]:
    value = table[key]
    if not isinstance(value, dict) or not all(
        isinstance(item, str) and item for item in value.values()
    ):
        raise _fail(key, "must be a table of nonempty strings")
    return dict(cast("dict[str, str]", value))


def _name_sets(table: Table, key: str) -> dict[str, frozenset[str]]:
    value = table[key]
    if not isinstance(value, dict):
        raise _fail(key, "must be a table of string lists")
    names: dict[str, frozenset[str]] = {}
    for path, functions in cast("dict[str, object]", value).items():
        if not isinstance(functions, list) or not functions:
            raise _fail(f"{key}.{path}", "must be a nonempty list of function names")
        if not all(isinstance(function, str) and function for function in functions):
            raise _fail(f"{key}.{path}", "must be a nonempty list of function names")
        names[path] = frozenset(cast("list[str]", functions))
    return names


def parse_python_quality(table: Table) -> PythonQuality:
    expected = set(PythonQuality.__dataclass_fields__)
    missing = sorted(expected - set(table))
    if missing:
        raise _fail(", ".join(missing), "is missing")
    unknown = sorted(set(table) - expected)
    if unknown:
        raise SettingsError(
            f"{PROOFS_FILE}: [{TABLE}] has unknown keys {', '.join(unknown)}; remove them"
        )
    return PythonQuality(
        maintained_roots=_strings(table, "maintained_roots"),
        skill_roots=_strings(table, "skill_roots"),
        excluded_paths=_string_map(table, "excluded_paths"),
        script_root=_string(table, "script_root"),
        script_module_directory=_string(table, "script_module_directory"),
        script_names=frozenset(_strings(table, "script_names")),
        test_root=_string(table, "test_root"),
        test_root_files=frozenset(_strings(table, "test_root_files")),
        test_groups=frozenset(_strings(table, "test_groups")),
        flat_test_groups=_string_map(table, "flat_test_groups"),
        non_package_directories=frozenset(_strings(table, "non_package_directories")),
        generic_name_parts=frozenset(_strings(table, "generic_name_parts")),
        generic_name_allowlist=frozenset(_strings(table, "generic_name_allowlist")),
        class_max_lines=_limit(table, "class_max_lines"),
        class_max_init_args=_limit(table, "class_max_init_args"),
        class_max_instance_attributes=_limit(table, "class_max_instance_attributes"),
        analyzer_bases=frozenset(_strings(table, "analyzer_bases")),
        project_analyzer_entrypoints=_string_map(table, "project_analyzer_entrypoints"),
        subprocess_wrappers=_name_sets(table, "subprocess_wrappers"),
        dynamic_import_adapters=frozenset(_strings(table, "dynamic_import_adapters")),
        contract_roots=_strings(table, "contract_roots"),
        max_loop_depth=_limit(table, "max_loop_depth"),
        analyzer_prefixes=_strings(table, "analyzer_prefixes"),
        ownership_markers=_strings(table, "ownership_markers"),
        filesystem_seams=_name_sets(table, "filesystem_seams"),
        environment_seams=_name_sets(table, "environment_seams"),
        fanout_root_packages=frozenset(_strings(table, "fanout_root_packages")),
        fanout_max=_limit(table, "fanout_max"),
        large_module_nloc=_limit(table, "large_module_nloc"),
        large_module_fanout_max=_limit(table, "large_module_fanout_max"),
    )


def _load(path: Path, hint: str) -> Table:
    try:
        return filesystem.read_toml(path)
    except FileNotFoundError as error:
        raise SettingsError(f"{path}: missing; {hint}") from error
    except tomllib.TOMLDecodeError as error:
        raise SettingsError(f"{path}: cannot parse: {error}; fix the TOML syntax") from error


def _section(document: Table, dotted: str, path: Path) -> Table:
    node: object = document
    for part in dotted.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    if not isinstance(node, dict):
        raise SettingsError(f"{path}: [{dotted}] table is missing; add it")
    return cast("Table", node)


def _shared_string(table: Table, key: str, owner: str, path: Path) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value:
        raise SettingsError(f"{path}: [{owner}] {key} must be a nonempty string; set it")
    return value


def _shared_strings(table: Table, key: str, owner: str, path: Path) -> list[str]:
    value = table.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise SettingsError(f"{path}: [{owner}] {key} must be a list of nonempty strings; set it")
    return list(cast("list[str]", value))


def composed_table(repo_root: Path = OWNING_REPO) -> Table:
    proofs = repo_root / PROOFS_FILE
    document = _load(proofs, f"add {PROOFS_FILE} with a [{TABLE}] table")
    pyproject = repo_root / "pyproject.toml"
    pytest_options = _section(
        _load(pyproject, "add pyproject.toml"), "tool.pytest.ini_options", pyproject
    )
    table = dict(_section(document, TABLE, proofs))
    layout = _section(document, "layout", proofs)
    structure = _section(document, "structure", proofs)
    testpaths = _shared_strings(pytest_options, "testpaths", "tool.pytest.ini_options", pyproject)
    if len(testpaths) != 1:
        raise SettingsError(f"{pyproject}: [tool.pytest.ini_options] testpaths must name one root")
    test_root = testpaths[0]
    fixtures_dir = _shared_string(layout, "fixtures_dir", "layout", proofs)
    support_dir = _shared_string(layout, "support_dir", "layout", proofs)
    flat = [
        pattern.removeprefix(f"{test_root}/")
        for pattern in _shared_strings(layout, "test_file_patterns", "layout", proofs)
        if pattern.startswith(f"{test_root}/") and "/" not in pattern.removeprefix(f"{test_root}/")
    ]
    excluded = table.get("excluded_paths", {})
    groups = table.get("test_groups", [])
    table["test_root"] = test_root
    table["generic_name_parts"] = _shared_strings(structure, "banned_words", "structure", proofs)
    table["excluded_paths"] = {
        **(excluded if isinstance(excluded, dict) else {}),
        fixtures_dir: f"{PROOFS_FILE} [layout] fixtures_dir",
    }
    table["test_groups"] = sorted({
        *(groups if isinstance(groups, list) else []),
        *(Path(fixtures_dir).name, Path(support_dir).name),
    })
    table["flat_test_groups"] = {pattern: pattern for pattern in flat}
    return table


@cache
def python_quality(repo_root: Path = OWNING_REPO) -> PythonQuality:
    return parse_python_quality(composed_table(repo_root))

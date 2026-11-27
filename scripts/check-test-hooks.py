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
    load_proofs,
    matches,
    string,
    strings,
    table,
    tracked_files,
    under,
)

RULE = "TEST.no-test-hooks"
SOURCE_PATTERNS = ("**/*.py", "**/*.ts", "**/*.tsx")
REFERENCE_PATTERNS = (
    *SOURCE_PATTERNS,
    "**/*.sh",
    "**/*.toml",
    "**/*.json",
    "**/*.yml",
    "**/*.yaml",
)
ENV_READERS = frozenset({"getenv", "get", "pop", "setdefault"})
TS_ENV_READ = re.compile(
    r"\b(?:process\.env|Bun\.env|import\.meta\.env)(?:\.(\w+)|\[\s*[\"'](\w+)[\"']\s*\])"
)
TS_MODULE_READ = re.compile(r"\b(?:require\.cache|import\.meta\.vitest)\b")
TS_EXPORT = re.compile(
    r"^export\s+(?:async\s+)?(?:function\*?|const|let|class)\s+([A-Za-z_$][\w$]*)", re.M
)
STRING_LITERAL = re.compile(r"[\"'`]([^\"'`]*)[\"'`]")
WORD_SPLIT = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])")


@dataclass(frozen=True, slots=True)
class Scope:
    root: Path
    production: list[str]
    tests: list[str]
    references: list[str]
    markers: frozenset[str]


def tokens(name: str) -> set[str]:
    return {part.lower() for part in WORD_SPLIT.split(name) if part}


def marker_tokens(layout_table: dict[str, object]) -> frozenset[str]:
    return frozenset(
        token.lower() for token in strings(layout_table, "test_marker_tokens", "layout")
    )


def load_scope(root: Path) -> Scope:
    layout_table = table(load_proofs(root), "layout")
    patterns = strings(layout_table, "test_file_patterns", "layout")
    support = string(layout_table, "support_dir", "layout")
    fixtures = string(layout_table, "fixtures_dir", "layout")
    support_files = strings(layout_table, "support_files", "layout")
    markers = marker_tokens(layout_table)
    test_root = support.rsplit("/", 1)[0]

    def is_test(path: str) -> bool:
        return bool(matches(path, [*patterns, *support_files]) or under(path, test_root))

    listed = [path for path in tracked_files(root, REFERENCE_PATTERNS) if not under(path, fixtures)]
    production = [path for path in listed if not is_test(path) and matches(path, SOURCE_PATTERNS)]
    tests = [path for path in listed if is_test(path) and matches(path, SOURCE_PATTERNS)]
    references = [path for path in listed if not is_test(path)]
    return Scope(root, production, tests, references, markers)


def _literal(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return node.id if isinstance(node, ast.Name) else ""


def _read_keys(node: ast.AST) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        owner = _dotted(node.func.value)
        key = _literal(node.args[0]) if node.args else None
        if key is not None and owner in {"os", "os.environ"} and node.func.attr in ENV_READERS:
            found.append(("environment", key))
        if key is not None and owner == "sys.modules" and node.func.attr == "get":
            found.append(("loaded module", key))
    elif isinstance(node, ast.Subscript):
        owner = _dotted(node.value)
        key = _literal(node.slice)
        if key is not None and owner in {"os.environ", "sys.modules"}:
            found.append(("environment" if owner == "os.environ" else "loaded module", key))
    elif isinstance(node, ast.Compare) and any(
        isinstance(op, (ast.In, ast.NotIn)) for op in node.ops
    ):
        key = _literal(node.left)
        owners = {_dotted(item) for item in node.comparators}
        if key is not None and owners & {"os.environ", "sys.modules"}:
            kind = "environment" if "os.environ" in owners else "loaded module"
            found.append((kind, key))
    return found


def python_findings(scope: Scope, path: str) -> list[str]:
    source = (scope.root / path).read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as error:
        return [f"{path}:{error.lineno or 0}: {RULE}: cannot parse the source; fix the syntax"]
    findings: list[str] = []
    for node in ast.walk(tree):
        for kind, key in _read_keys(node):
            if tokens(key) & scope.markers:
                findings.append(
                    f"{path}:{getattr(node, 'lineno', 0)}: {RULE}: reads the test marker {key} "
                    f"from the {kind}; take the behaviour from real configuration or an input "
                    "the product already has"
                )
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            findings.extend(
                f"{path}:{argument.lineno}: {RULE}: {node.name} declares the test-only parameter "
                f"{argument.arg}; drive the behaviour through a real input or an adapter seam"
                for argument in arguments
                if tokens(argument.arg) & scope.markers
            )
    return findings


def _environment_findings(scope: Scope, path: str, number: int, line: str) -> list[str]:
    values = {token for literal in STRING_LITERAL.findall(line) for token in tokens(literal)}
    keys = [match.group(1) or match.group(2) or "" for match in TS_ENV_READ.finditer(line)]
    return [
        f"{path}:{number}: {RULE}: reads the test marker {key} from the environment; "
        "take the behaviour from real configuration"
        for key in keys
        if (tokens(key) | values) & scope.markers
    ]


def typescript_findings(scope: Scope, path: str) -> list[str]:
    findings: list[str] = []
    for number, line in enumerate((scope.root / path).read_text(encoding="utf-8").splitlines(), 1):
        findings.extend(_environment_findings(scope, path, number, line))
        if TS_MODULE_READ.search(line):
            findings.append(
                f"{path}:{number}: {RULE}: reads the loaded test modules; remove the test branch"
            )
    return findings


def _python_symbols(source: str) -> list[tuple[str, int]]:
    tree = ast.parse(source)
    symbols: list[tuple[str, int]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.append((node.name, node.lineno))
        elif isinstance(node, ast.Assign):
            symbols.extend((t.id, node.lineno) for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            symbols.append((node.target.id, node.lineno))
    return [(name, line) for name, line in symbols if not name.startswith("__")]


def _declared(scope: Scope, path: str) -> list[tuple[str, int]]:
    source = (scope.root / path).read_text(encoding="utf-8")
    if path.endswith(".py"):
        try:
            return _python_symbols(source)
        except SyntaxError:
            return []
    return [(m.group(1), source.count("\n", 0, m.start()) + 1) for m in TS_EXPORT.finditer(source)]


def _word_counts(scope: Scope, paths: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in paths:
        for word in re.findall(
            r"[A-Za-z_$][\w$]*", (scope.root / path).read_text(encoding="utf-8")
        ):
            counts[word] = counts.get(word, 0) + 1
    return counts


def test_only_findings(scope: Scope) -> list[str]:
    production_words = _word_counts(scope, scope.references)
    test_words = _word_counts(scope, scope.tests)
    findings: list[str] = []
    for path in scope.production:
        for name, line in _declared(scope, path):
            if production_words.get(name, 0) <= 1 and test_words.get(name, 0) > 0:
                findings.append(
                    f"{path}:{line}: {RULE}: {name} is referenced only from tests; delete it, or "
                    "make the tests reach the behaviour through the production entry point"
                )
    return findings


def main(argv: list[str]) -> int:
    try:
        scope = load_scope(Path.cwd())
    except (SettingsError, subprocess.CalledProcessError, OSError) as error:
        print(f"{RULE}: {error}", file=sys.stderr)
        return 1
    paths = argv or scope.production
    findings: list[str] = []
    for path in paths:
        if not (scope.root / path).is_file():
            findings.append(f"{path}: {RULE}: no such production source; pass an existing path")
        elif path.endswith(".py"):
            findings.extend(python_findings(scope, path))
        elif path.endswith((".ts", ".tsx")):
            findings.extend(typescript_findings(scope, path))
    if not argv:
        findings.extend(test_only_findings(scope))
    for finding in findings:
        print(finding, file=sys.stderr)
    print(f"check-test-hooks: {len(paths)} files, {len(findings)} violations", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

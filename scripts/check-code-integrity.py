#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import bisect
import io
import re
import subprocess
import sys
import tokenize
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from lib import filesystem

COMMENTS = "CODE.no-comments"
PLACEHOLDERS = "CODE.no-placeholder-bodies"
MOCKS = "CODE.no-product-mocks"
PROOFS_FILE = "proofs.toml"
LAYOUT_KEYS = ("test_file_patterns", "support_dir", "fixtures_dir")

_DIRECTIVE = re.compile(
    r"""^(?:
        noqa\b | NOQA\b | type:\s*ignore\b | pyright:\s* | fmt:\s*(?:on|off|skip)\b
        | ruff:\s*(?:noqa|isort)\b | isort:\s*\w | pylint:\s*\w | mypy:\s*\w
        | (?:eslint|oxlint)(?:-[a-z]+(?:-[a-z]+)*)?(?:\s|$)
        | @ts-(?:expect-error|ignore|nocheck|check)\b | <reference\b
        | prettier-ignore\b | biome-ignore\b | markdownlint-[a-z-]+\b
        | shellcheck\s+\w | go:build\b | nolint\b | SPDX-License-Identifier:
        | (?:vim?|ex):\s | -\*-.*-\*-
        | (?:BEGIN|END)\ GENERATED\ FROM\s
    )""",
    re.VERBOSE,
)
_CODING = re.compile(r"^[ \t\f]*#.*?coding[:=][ \t]*[-\w.]+")
_TOML = re.compile(
    r'"""(?:\\.|[^\\])*?"""' r"|'''.*?'''" r'|"(?:\\.|[^"\\\n])*"' r"|'[^'\n]*'|(?P<c>#[^\n]*)",
    re.S,
)
_SHELL = re.compile(
    r"\$'(?:\\.|[^'\\])*'|'[^']*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`|\\."
    r"|(?<![^\s;&|()<>])(?P<c>#[^\n]*)"
    r"|(?P<h>(?<!<)<<(?!<)-?[ \t]*(?P<q>['\"]?)(?P<w>[A-Za-z_][\w-]*)(?P=q))"
)
_YAML_LINE = re.compile(
    r"(?<![^\s\[{,])(?:'(?:''|[^'])*'|" r'"(?:\\.|[^"\\])*")' r"|(?<!\S)(?P<c>#.*)"
)
_YAML_BLOCK = re.compile(r"(?:^\s*-|:)\s+[|>][-+1-9]*$|^\s*-?\s*[|>][-+1-9]*$")
_SQL = re.compile(
    r"'(?:''|\\.|[^'\\])*'|" r'"(?:""|[^"])*"' r"|`[^`]*`|(?P<c>--[^\n]*|/\*.*?\*/)",
    re.S,
)
_CSS = re.compile(r"'(?:\\.|[^'\\\n])*'|" r'"(?:\\.|[^"\\\n])*"' r"|(?P<c>/\*.*?\*/)", re.S)
_JSONC = re.compile(r'"(?:\\.|[^"\\\n])*"' r"|(?P<c>//[^\n]*|/\*.*?\*/)", re.S)
_MARKUP = re.compile(r"(?P<c><!--.*?-->)", re.S)
_HASH_LINE = re.compile(r"^[ \t]*(?P<c>#[^\n]*)", re.M)
_INI_LINE = re.compile(r"^[ \t]*(?P<c>[#;][^\n]*)", re.M)
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`).+?(?<!`)\1(?!`)")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_JS_IMPORT = re.compile(
    r"\b(?:import|export)\s+(?:[^'\";]*?\bfrom\s+)?['\"](?P<m>[^'\"]+)['\"]"
    r"|\b(?:require|import)\s*\(\s*['\"](?P<r>[^'\"]+)['\"]\s*\)"
)
_JS_MOCK_CALL = re.compile(
    r"\b(?P<call>(?:jest|vi)\s*\.\s*(?:mock|doMock|unstable_mockModule))\s*\("
)
_JS_WORD = re.compile(r"[A-Za-z_$][\w$]*|\d[\w.]*")
_JS_REGEX = re.compile(r"/(?:\\.|\[(?:\\.|[^\]\\\n])*\]|[^/\\\n\[])*/?")
_JS_TEMPLATE = re.compile(r"(?:\\.|[^`\\$]|\$(?!\{))*(`|\$\{)?", re.S)
_JS_QUOTED = {
    quote: re.compile(rf"(?:\\.|[^{quote}\\\n])*(?:{quote}|$)", re.M | re.S) for quote in "'\""
}
_REGEX_PREFIX = frozenset("(,=:[!&|?{};+-*%<>~^")
_REGEX_KEYWORDS = frozenset({
    "return",
    "typeof",
    "case",
    "in",
    "of",
    "delete",
    "void",
    "throw",
    "new",
    "yield",
    "await",
})
_PY_MOCK_MODULES = ("unittest.mock", "mock", "pytest_mock")
_JS_MOCK_MODULES = (
    "sinon",
    "jest-mock",
    "ts-mockito",
    "testdouble",
    "mock-require",
    "proxyquire",
)
_SCRIPT_SUFFIXES = frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts"})
_NO_COMMENT_SUFFIXES = frozenset({".json", ".txt", ".csv"})
_UNREADABLE: set[int] = {-1}

type Lexer = Callable[[str], list[Span]]
type Target = tuple[str, str, set[int] | None]


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    kind: str


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    message: str

    def render(self) -> str:
        location = f"{self.path}:{self.line}" if self.line else self.path
        return f"{location}: {self.rule}: {self.message}"


@dataclass(frozen=True)
class Layout:
    root: PurePosixPath
    patterns: tuple[re.Pattern[str], ...]
    dirs: tuple[str, ...]

    def is_test_path(self, path: PurePosixPath) -> bool:
        relative = path.relative_to(self.root).as_posix()
        if any(relative == item or relative.startswith(f"{item}/") for item in self.dirs):
            return True
        return any(pattern.fullmatch(relative) for pattern in self.patterns)


class LayoutError(Exception):
    pass


def glob_regex(pattern: str) -> re.Pattern[str]:
    parts = re.split(r"(\*\*/|\*\*|\*|\?)", pattern)
    table = {"**/": "(?:.*/)?", "**": ".*", "*": "[^/]*", "?": "[^/]"}
    return re.compile("".join(table.get(part, re.escape(part)) for part in parts))


def _pattern_spans(text: str, pattern: re.Pattern[str]) -> list[Span]:
    return [
        Span(match.start("c"), match.end("c"), "comment")
        for match in pattern.finditer(text)
        if match.group("c") is not None
    ]


def _shell_spans(text: str) -> list[Span]:
    spans: list[Span] = []
    pending: list[str] = []
    position = 0
    while (match := _SHELL.search(text, position)) is not None:
        position = match.end()
        if match.group("c") is not None:
            spans.append(Span(match.start("c"), match.end("c"), "comment"))
        elif match.group("h") is not None:
            pending.append(match.group("w"))
        line_end = text.find("\n", position)
        rest_of_line = text[position:] if line_end == -1 else text[position:line_end]
        if pending and _SHELL.search(rest_of_line) is None:
            position = _skip_heredocs(text, line_end, pending)
            pending = []
    return spans


def _skip_heredocs(text: str, line_end: int, words: list[str]) -> int:
    position = len(text) if line_end == -1 else line_end + 1
    for word in words:
        closing = re.compile(rf"^[ \t]*{re.escape(word)}[ \t]*$", re.M).search(text, position)
        position = len(text) if closing is None else closing.end()
    return position


def _yaml_spans(text: str) -> list[Span]:
    spans: list[Span] = []
    block_indent: int | None = None
    offset = 0
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        indent = len(body) - len(body.lstrip(" "))
        start = offset
        offset += len(line)
        if block_indent is not None and (not body.strip() or indent > block_indent):
            continue
        block_indent = None
        comments = [match for match in _YAML_LINE.finditer(body) if match.group("c") is not None]
        code_end = comments[0].start("c") if comments else len(body)
        if comments:
            spans.append(Span(start + code_end, start + len(body), "comment"))
        if _YAML_BLOCK.search(body[:code_end].rstrip()):
            block_indent = indent
    return spans


def _blank(text: str) -> str:
    return re.sub(r"[^\n]", " ", text)


def _markdown_spans(text: str) -> list[Span]:
    masked: list[str] = []
    fence: str | None = None
    for line in text.splitlines(keepends=True):
        opener = _FENCE.match(line)
        if fence is None and opener is None:
            masked.append(_CODE_SPAN.sub(lambda match: _blank(match.group(0)), line))
            continue
        masked.append(_blank(line))
        if fence is None and opener is not None:
            fence = opener.group(1)
        elif opener is not None and fence is not None and opener.group(1).startswith(fence):
            fence = None
    return _pattern_spans("".join(masked), _MARKUP)


class ScriptLexer:
    def __init__(self, text: str) -> None:
        self.text = text
        self.position = 0
        self.previous = ""
        self.templates: list[int] = []
        self.spans: list[Span] = []
        self.strings: list[Span] = []

    def run(self) -> list[Span]:
        while self.position < len(self.text):
            self._step()
        return self.spans

    def _step(self) -> None:
        char = self.text[self.position]
        if char in _JS_QUOTED:
            self._quoted(char)
        elif char == "`":
            self.position += 1
            self._template()
        elif char == "/":
            self._slash()
        elif char in "{}":
            self._brace(char)
        else:
            self._token(char)

    def _token(self, char: str) -> None:
        word = _JS_WORD.match(self.text, self.position)
        if word:
            self.previous = word.group(0)
            self.position = word.end()
            return
        if not char.isspace():
            self.previous = char
        self.position += 1

    def _brace(self, char: str) -> None:
        self.position += 1
        self.previous = char
        if not self.templates:
            return
        if char == "{":
            self.templates[-1] += 1
        elif self.templates[-1] == 0:
            self.templates.pop()
            self._template()
        else:
            self.templates[-1] -= 1

    def _quoted(self, quote: str) -> None:
        start = self.position
        match = _JS_QUOTED[quote].match(self.text, self.position + 1)
        self.position = len(self.text) if match is None else max(match.end(), self.position + 1)
        self.strings.append(Span(start, self.position, "string"))
        self.previous = quote

    def _template(self) -> None:
        match = _JS_TEMPLATE.match(self.text, self.position)
        end = len(self.text) if match is None or match.group(1) is None else match.end()
        self.strings.append(Span(self.position, end, "string"))
        self.position = end
        if match is None or match.group(1) is None:
            return
        self.previous = "`"
        if match.group(1) == "${":
            self.templates.append(0)
            self.previous = "{"

    def _slash(self) -> None:
        following = self.text[self.position + 1 : self.position + 2]
        if following in {"/", "*"}:
            closer = "\n" if following == "/" else "*/"
            found = self.text.find(closer, self.position + 2)
            end = len(self.text) if found == -1 else found + len(closer) - (following == "/")
            self.spans.append(Span(self.position, end, "comment"))
            self.position = end
            return
        if not self.previous or self.previous in _REGEX_PREFIX | _REGEX_KEYWORDS:
            match = _JS_REGEX.match(self.text, self.position)
            self.position = self.position + 1 if match is None else match.end()
            self.previous = "/re"
            return
        self.previous = "/"
        self.position += 1


def _script_spans(text: str) -> list[Span]:
    return ScriptLexer(text).run()


def python_docstrings(tree: ast.AST) -> list[tuple[ast.AST, ast.Expr]]:
    found: list[tuple[ast.AST, ast.Expr]] = []
    for node in ast.walk(tree):
        docstring = docstring_node(node)
        if docstring is not None:
            found.append((node, docstring))
    return found


def _python_spans(text: str) -> list[Span]:
    starts = line_starts(text)
    spans = [
        Span(
            starts[token.start[0] - 1] + token.start[1],
            starts[token.end[0] - 1] + token.end[1],
            "comment",
        )
        for token in tokenize.generate_tokens(io.StringIO(text).readline)
        if token.type == tokenize.COMMENT
    ]
    for _, docstring in python_docstrings(ast.parse(text)):
        end_line = docstring.end_lineno or docstring.lineno
        start = starts[docstring.lineno - 1] + docstring.col_offset
        end = starts[end_line - 1] + (docstring.end_col_offset or 0)
        spans.append(Span(start, end, "docstring"))
    return sorted(spans, key=lambda span: span.start)


def docstring_node(node: ast.AST) -> ast.Expr | None:
    if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
        return None
    first = node.body[0] if node.body else None
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return first
    return None


def line_starts(text: str) -> list[int]:
    starts = [0]
    starts.extend(match.end() for match in re.finditer(r"\n", text))
    return starts


def _no_spans(_text: str) -> list[Span]:
    return []


def _by_pattern(pattern: re.Pattern[str]) -> Lexer:
    return lambda text: _pattern_spans(text, pattern)


_BY_SUFFIX: dict[str, Lexer] = {
    **dict.fromkeys((".py", ".pyi"), _python_spans),
    **dict.fromkeys(_SCRIPT_SUFFIXES, _script_spans),
    **dict.fromkeys((".sh", ".bash", ".zsh"), _shell_spans),
    **dict.fromkeys((".yaml", ".yml"), _yaml_spans),
    ".toml": _by_pattern(_TOML),
    ".sql": _by_pattern(_SQL),
    ".css": _by_pattern(_CSS),
    **dict.fromkeys((".jsonc", ".json5"), _by_pattern(_JSONC)),
    **dict.fromkeys((".html", ".htm", ".svg", ".xml"), _by_pattern(_MARKUP)),
    **dict.fromkeys((".md", ".markdown"), _markdown_spans),
    **dict.fromkeys((".service", ".timer", ".ini", ".cfg", ".conf"), _by_pattern(_INI_LINE)),
    **dict.fromkeys(_NO_COMMENT_SUFFIXES, _no_spans),
}
_BY_NAME: dict[str, Lexer] = {
    **dict.fromkeys(
        (".gitignore", ".gitattributes", ".dockerignore", "CODEOWNERS", "Dockerfile", "Makefile"),
        _by_pattern(_HASH_LINE),
    ),
    "CMakeLists.txt": _by_pattern(_HASH_LINE),
    ".shellcheckrc": _by_pattern(_HASH_LINE),
    ".editorconfig": _by_pattern(_INI_LINE),
    "uv.lock": _by_pattern(_TOML),
    "bun.lock": _by_pattern(_JSONC),
}


def lexer_for(path: PurePosixPath) -> Lexer | None:
    return _BY_NAME.get(path.name) or _BY_SUFFIX.get(path.suffix)


def is_directive(text: str, span: Span) -> bool:
    if span.kind == "docstring":
        return False
    raw = text[span.start : span.end]
    if span.start == 0 and raw.startswith("#!"):
        return True
    if text.count("\n", 0, span.start) < 2 and _CODING.match(raw):
        return True
    body = re.sub(r"^(?:#+|//+|/\*+|<!--|--|;)\s*", "", raw)
    return bool(_DIRECTIVE.match(body))


def comment_spans(path: PurePosixPath, text: str) -> list[Span]:
    lexer = lexer_for(path)
    if lexer is None:
        raise LookupError(path.as_posix())
    return [span for span in lexer(text) if not is_directive(text, span)]


def _lines_of(starts: list[int], span: Span) -> range:
    first = bisect.bisect_right(starts, span.start)
    last = bisect.bisect_right(starts, max(span.start, span.end - 1))
    return range(first, last + 1)


def _comment_findings(path: PurePosixPath, text: str, scope: set[int] | None) -> list[Finding]:
    starts = line_starts(text)
    findings: list[Finding] = []
    for span in comment_spans(path, text):
        lines = _lines_of(starts, span)
        if scope is not None and scope.isdisjoint(lines):
            continue
        snippet = " ".join(text[span.start : span.end].split())[:60]
        findings.append(
            Finding(
                path.as_posix(),
                lines.start,
                COMMENTS,
                f"{span.kind} '{snippet}'; remove it and carry the intent in names, types or "
                "tests (tool directives are exempt)",
            )
        )
    return findings


def _decorator_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names: set[str] = set()
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute):
            names.add(target.attr)
        elif isinstance(target, ast.Name):
            names.add(target.id)
    return names


def is_protocol(node: ast.ClassDef) -> bool:
    for base in node.bases:
        target = base.value if isinstance(base, ast.Subscript) else base
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
        if name == "Protocol":
            return True
    return False


def is_declaration(node: ast.FunctionDef | ast.AsyncFunctionDef, in_protocol: bool) -> bool:
    return in_protocol or not _decorator_names(node).isdisjoint({"abstractmethod", "overload"})


def placeholder_kind(statement: ast.stmt) -> str | None:
    if isinstance(statement, ast.Pass):
        return "pass"
    if (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and statement.value.value is Ellipsis
    ):
        return "..."
    if isinstance(statement, ast.Raise) and statement.exc is not None:
        raised = statement.exc.func if isinstance(statement.exc, ast.Call) else statement.exc
        if isinstance(raised, ast.Name) and raised.id == "NotImplementedError":
            return "raise NotImplementedError"
    return None


def _placeholder_functions(
    tree: ast.AST, in_protocol: bool = False
) -> Iterable[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str]]:
    for child in ast.iter_child_nodes(tree):
        if isinstance(child, ast.ClassDef):
            yield from _placeholder_functions(child, is_protocol(child))
            continue
        if not isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
            yield from _placeholder_functions(child)
            continue
        body = child.body[1:] if docstring_node(child) is not None else child.body
        kind = placeholder_kind(body[0]) if len(body) == 1 else None
        if kind is not None and not is_declaration(child, in_protocol):
            yield child, kind
        yield from _placeholder_functions(child)


def _placeholder_findings(
    path: PurePosixPath, tree: ast.Module, scope: set[int] | None
) -> list[Finding]:
    findings: list[Finding] = []
    for node, kind in _placeholder_functions(tree):
        body_lines = range(node.body[0].lineno, (node.end_lineno or node.lineno) + 1)
        if scope is not None and scope.isdisjoint(body_lines):
            continue
        findings.append(
            Finding(
                path.as_posix(),
                node.lineno,
                PLACEHOLDERS,
                f"function '{node.name}' has only a placeholder body ({kind}); implement it, "
                "or declare it in a Protocol, with @abstractmethod or with @overload",
            )
        )
    return findings


def _library(name: str, libraries: tuple[str, ...], separator: str) -> str | None:
    for library in libraries:
        if name == library or name.startswith(f"{library}{separator}"):
            return library
    return None


def _imported_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
        return [node.module, *(f"{node.module}.{alias.name}" for alias in node.names)]
    return []


def _python_mock_uses(tree: ast.Module) -> Iterable[tuple[int, str]]:
    for node in ast.walk(tree):
        libraries = {_library(name, _PY_MOCK_MODULES, ".") for name in _imported_names(node)}
        for library in sorted(item for item in libraries if item is not None):
            yield getattr(node, "lineno", 0), f"imports mock library '{library}'"


def _script_mock_uses(text: str) -> Iterable[tuple[int, str]]:
    lexer = ScriptLexer(text)
    lexer.run()
    code = list(text)
    for span in lexer.spans:
        code[span.start : span.end] = _blank(text[span.start : span.end])
    stripped = "".join(code)
    for span in lexer.strings:
        code[span.start : span.end] = _blank(text[span.start : span.end])
    calls = "".join(code)
    for match in _JS_IMPORT.finditer(stripped):
        library = _library(match.group("m") or match.group("r"), _JS_MOCK_MODULES, "/")
        if library is not None:
            yield stripped.count("\n", 0, match.start()) + 1, f"imports mock library '{library}'"
    for match in _JS_MOCK_CALL.finditer(calls):
        call = re.sub(r"\s", "", match.group("call"))
        yield calls.count("\n", 0, match.start()) + 1, f"calls {call}()"


def _mock_findings(
    path: PurePosixPath, uses: Iterable[tuple[int, str]], scope: set[int] | None
) -> list[Finding]:
    return [
        Finding(
            path.as_posix(),
            line,
            MOCKS,
            f"product code {what}; keep test doubles under a test, support or fixture path "
            f"that {PROOFS_FILE} [layout] declares",
        )
        for line, what in uses
        if scope is None or line in scope
    ]


class Layouts:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.cache: dict[PurePosixPath, Layout | None] = {}

    def for_path(self, path: PurePosixPath) -> Layout:
        for directory in path.parents:
            layout = self._load(directory)
            if layout is not None:
                return layout
        raise LayoutError(
            f"no {PROOFS_FILE} [layout] covers {path}; declare {', '.join(LAYOUT_KEYS)} "
            f"in the root {PROOFS_FILE} [layout]"
        )

    def _load(self, directory: PurePosixPath) -> Layout | None:
        if directory not in self.cache:
            config = self.root / directory / PROOFS_FILE
            self.cache[directory] = (
                parse_layout(config, directory) if filesystem.is_file(config) else None
            )
        return self.cache[directory]


def parse_layout(config: Path, directory: PurePosixPath) -> Layout:
    shown = (directory / PROOFS_FILE).as_posix()
    try:
        layout = tomllib.loads(filesystem.read_text(config)).get("layout")
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise LayoutError(f"{shown} cannot be read ({error}); fix its TOML") from error
    if not isinstance(layout, dict):
        raise LayoutError(f"{shown} has no [layout]; declare {', '.join(LAYOUT_KEYS)} there")
    missing = [key for key in LAYOUT_KEYS if key not in layout]
    if missing:
        raise LayoutError(f"{shown} [layout] lacks {', '.join(missing)}; declare it")
    patterns = layout["test_file_patterns"]
    dirs = [layout["support_dir"], layout["fixtures_dir"]]
    if not isinstance(patterns, list) or not all(isinstance(item, str) for item in patterns):
        raise LayoutError(f"{shown} [layout] test_file_patterns must be a list of globs")
    if not all(isinstance(item, str) and item.strip("/") for item in dirs):
        raise LayoutError(f"{shown} [layout] support_dir and fixtures_dir must be paths")
    return Layout(
        directory,
        tuple(glob_regex(item) for item in patterns),
        tuple(str(item).strip("/") for item in dirs),
    )


def _python_findings(
    path: PurePosixPath, text: str, scope: set[int] | None, layouts: Layouts
) -> list[Finding]:
    tree = ast.parse(text)
    findings = _comment_findings(path, text, scope)
    if path.suffix != ".pyi":
        findings += _placeholder_findings(path, tree, scope)
    if not layouts.for_path(path).is_test_path(path):
        findings += _mock_findings(path, _python_mock_uses(tree), scope)
    return findings


def check_text(
    path: PurePosixPath, text: str, scope: set[int] | None, layouts: Layouts
) -> list[Finding]:
    try:
        if path.suffix in {".py", ".pyi"}:
            return _python_findings(path, text, scope, layouts)
        findings = _comment_findings(path, text, scope)
        if path.suffix in _SCRIPT_SUFFIXES and not layouts.for_path(path).is_test_path(path):
            findings += _mock_findings(path, _script_mock_uses(text), scope)
    except LookupError:
        message = "no comment syntax is known for this file type; add its lexer to "
        return [Finding(path.as_posix(), 0, COMMENTS, f"{message}{sys.argv[0]}")]
    except (SyntaxError, tokenize.TokenError) as error:
        return [Finding(path.as_posix(), 0, COMMENTS, f"cannot parse ({error}); fix the syntax")]
    except LayoutError as error:
        return [Finding(PROOFS_FILE, 0, MOCKS, str(error))]
    return findings


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", "-c", "core.quotePath=false", *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return completed.stdout


def added_lines(diff: str) -> dict[str, set[int]]:
    added: dict[str, set[int]] = {}
    current: str | None = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = None if line == "+++ /dev/null" else line.removeprefix("+++ b/")
            continue
        hunk = _HUNK.match(line)
        if hunk and current is not None:
            start, count = int(hunk.group(1)), int(hunk.group(2) or "1")
            added.setdefault(current, set()).update(range(start, start + count))
    return added


def _diff_targets(staged: bool, base: str | None, pathspec: list[str]) -> list[Target]:
    if staged:
        revisions, source = ["--cached"], ":"
    else:
        revisions, source = [_git("merge-base", str(base), "HEAD").strip(), "HEAD"], "HEAD:"
    diff = _git(
        "diff",
        "-U0",
        "--no-color",
        "--no-ext-diff",
        "--diff-filter=AMR",
        *revisions,
        "--",
        *pathspec,
    )
    return [
        (path, _git("show", f"{source}{path}"), lines)
        for path, lines in sorted(added_lines(diff).items())
        if not filesystem.is_symlink(Path(path))
    ]


def _regular(path: Path) -> bool:
    return filesystem.is_file(path) and not filesystem.is_symlink(path)


def _explicit_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = Path(raw)
        members = sorted(filesystem.rglob(path, "*")) if filesystem.is_dir(path) else [path]
        files.extend(member for member in members if _regular(member))
    return files


def _tracked_files(pathspec: list[str]) -> list[Path]:
    listed = _git("ls-files", "-z", "--", *pathspec).split("\0")
    return [Path(item) for item in listed if item and _regular(Path(item))]


def _whole_files(files: list[Path]) -> list[Target]:
    targets: list[Target] = []
    for file in files:
        try:
            targets.append((file.as_posix(), file.read_text(encoding="utf-8"), None))
        except UnicodeDecodeError:
            targets.append((file.as_posix(), "", _UNREADABLE))
    return targets


def check_targets(targets: Iterable[Target], root: Path) -> list[Finding]:
    layouts = Layouts(root)
    findings: set[Finding] = set()
    for raw, text, scope in targets:
        if scope is _UNREADABLE:
            message = "file is not UTF-8 text; store it as UTF-8 text or remove it"
            findings.add(Finding(raw, 0, COMMENTS, message))
            continue
        findings.update(check_text(PurePosixPath(raw), text, scope, layouts))
    return sorted(findings, key=lambda item: (item.path, item.line, item.rule, item.message))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="check-code-integrity.py")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--all", action="store_true", help="every tracked file the pathspec selects")
    mode.add_argument("--staged", action="store_true", help="added lines of the staged diff")
    mode.add_argument("--base", help="added lines from the merge base with BASE to HEAD")
    parser.add_argument("paths", nargs="*", help="files or directories, or a git pathspec")
    return parser


def targets_for(options: argparse.Namespace) -> list[Target] | None:
    if options.staged or options.base:
        return _diff_targets(options.staged, options.base, options.paths)
    if options.all:
        return _whole_files(_tracked_files(options.paths))
    if options.paths:
        return _whole_files(_explicit_files(options.paths))
    return None


def main(argv: list[str]) -> int:
    parser = _parser()
    targets = targets_for(parser.parse_args(argv))
    if targets is None:
        parser.print_usage(sys.stderr)
        return 2
    findings = check_targets(targets, Path.cwd())
    for finding in findings:
        print(finding.render(), file=sys.stderr)
    print(f"check-code-integrity: {len(targets)} files, {len(findings)} findings", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

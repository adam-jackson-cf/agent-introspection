from __future__ import annotations

import copy
import json
import math
import os
import re
import shutil
import tempfile
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from functools import partial
from pathlib import Path
from typing import Any, cast

FORMATS = ("json", "toml", "dotenv")
type Values = Mapping[str, object]
NEW_FILE_MODE = 0o600
_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
_QUOTED = re.compile(r"\"(?:[^\"\\\n]|\\.)*\"|'[^'\n]*'")
_STRING_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}
_DOTENV_LINE = re.compile(r"^(?P<lead>\s*(?:export\s+)?)(?P<key>[A-Za-z_][A-Za-z0-9_.]*)\s*=")
_DOTENV_QUOTED = re.compile(
    r"""^(?P<quote>["'])(?P<inner>.*?)(?P=quote)(?P<comment>\s*(?:#.*)?)$"""
)
_DOTENV_COMMENT = re.compile(r"\s+#")
_DOTENV_BARE = re.compile(r"[A-Za-z0-9_./:@%+,=-]*")
_DOTENV_ESCAPES = {'"': '"', "\\": "\\", "n": "\n"}


class ConfigFileError(ValueError):
    pass


@dataclass(frozen=True)
class TableUpdate:
    text: str
    notes: tuple[str, ...] = ()


def _table_path(table: str) -> tuple[str, ...]:
    return tuple(table.split(".")) if table else ()


def _descend(document: Mapping[str, Any], path: tuple[str, ...], where: str) -> dict[str, Any]:
    node: object = document
    for depth, part in enumerate(path):
        node = cast(Mapping[str, Any], node).get(part, {})
        if not isinstance(node, dict):
            raise ConfigFileError(f"{where}: {'.'.join(path[: depth + 1])} is not a table")
    return cast(dict[str, Any], node)


def _with_values(
    document: Mapping[str, Any], path: tuple[str, ...], values: Values
) -> dict[str, Any]:
    updated = copy.deepcopy(dict(document))
    node = updated
    for part in path:
        node = node.setdefault(part, {})
    node.update(copy.deepcopy(dict(values)))
    return updated


def _json_document(text: str) -> dict[str, Any]:
    if not text.strip():
        return {}
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise ConfigFileError(f"not valid JSON: {error}") from None
    if not isinstance(document, dict):
        raise ConfigFileError("the file must hold a JSON object")
    return document


def _json_indent(text: str) -> str | None:
    for line in text.split("\n")[1:]:
        content = line.lstrip(" \t")
        if content and len(content) < len(line):
            return line[: len(line) - len(content)]
    return None if text.strip() and "\n" not in text.strip() else "  "


def render_json(document: Mapping[str, Any], like: str) -> str:
    rendered = json.dumps(document, indent=_json_indent(like), ensure_ascii=False)
    return rendered + ("\n" if like.endswith("\n") or not like else "")


def _update_json(text: str, path: tuple[str, ...], values: Values) -> str:
    document = _json_document(text)
    _descend(document, path, "JSON")
    return render_json(_with_values(document, path, values), text)


def _toml_string(value: str) -> str:
    characters = [
        _STRING_ESCAPES.get(char)
        or (f"\\u{ord(char):04x}" if ord(char) < 0x20 or ord(char) == 0x7F else char)
        for char in value
    ]
    return '"' + "".join(characters) + '"'


def _toml_float(value: float) -> str:
    if math.isnan(value):
        return "nan"
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    return repr(value)


def toml_key(key: str) -> str:
    return key if _BARE_KEY.fullmatch(key) else _toml_string(key)


_SCALARS: tuple[tuple[type | tuple[type, ...], Callable[[Any], str]], ...] = (
    (bool, lambda value: "true" if value else "false"),
    (int, str),
    (float, _toml_float),
    (str, _toml_string),
    ((date, time), lambda value: value.isoformat()),
)


def toml_value(value: object) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        items = ", ".join(
            f"{toml_key(str(key))} = {toml_value(item)}" for key, item in value.items()
        )
        return "{ " + items + " }" if items else "{}"
    for kind, render in _SCALARS:
        if isinstance(value, kind):
            return render(value)
    raise ConfigFileError(f"cannot write a {type(value).__name__} as TOML")


@dataclass(frozen=True)
class _Statement:
    first: int
    last: int
    table: tuple[str, ...]
    key: str = ""
    header: bool = False
    dotted: bool = False


def _lines(text: str) -> list[str]:
    parts = text.split("\n")
    return [part + "\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def _toml_document(text: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigFileError(f"not valid TOML: {error}") from None


def _statement_end(lines: list[str], first: int) -> tuple[int, dict[str, Any]]:
    for last in range(first, len(lines)):
        try:
            return last, tomllib.loads("".join(lines[first : last + 1]))
        except tomllib.TOMLDecodeError:
            continue
    raise ConfigFileError(f"cannot read the TOML statement on line {first + 1}")


def _header_path(parsed: Mapping[str, Any]) -> tuple[str, ...]:
    path: list[str] = []
    node: object = parsed
    while isinstance(node, dict) and len(node) == 1:
        key, node = next(iter(node.items()))
        path.append(key)
    return tuple(path)


def _masked(line: str) -> str:
    return _QUOTED.sub(lambda match: "_" * len(match.group()), line)


def _statements(lines: list[str]) -> list[_Statement]:
    statements: list[_Statement] = []
    table: tuple[str, ...] = ()
    index = 0
    while index < len(lines):
        content = lines[index].strip()
        if not content or content.startswith("#"):
            index += 1
            continue
        last, parsed = _statement_end(lines, index)
        if content.startswith("["):
            table = _header_path(parsed)
            statements.append(_Statement(index, last, table, header=True))
        else:
            dotted = "." in _masked(lines[index]).split("=", 1)[0]
            statements.append(_Statement(index, last, table, next(iter(parsed)), dotted=dotted))
        index = last + 1
    return statements


def _defined_elsewhere(statements: list[_Statement], path: tuple[str, ...]) -> bool:
    return any(
        not statement.header
        and len(statement.table) < len(path)
        and path[: len(statement.table) + 1] == (*statement.table, statement.key)
        for statement in statements
    )


def _newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _key_line(line: str, value: object) -> str:
    masked = _masked(line)
    equals = masked.index("=")
    body = line.rstrip("\r\n")
    ending = line[len(body) :]
    after = body[equals + 1 :]
    gap = after[: len(after) - len(after.lstrip(" \t"))]
    comment = masked.find("#", equals + 1)
    trailing = " " + body[comment:] if comment != -1 else ""
    return f"{body[: equals + 1]}{gap}{toml_value(value)}{trailing}{ending}"


class _UnsafeEditError(ConfigFileError):
    pass


def _edit_in_place(
    lines: list[str], statements: list[_Statement], path: tuple[str, ...], values: Values
) -> str:
    header = next((item for item in statements if item.header and item.table == path), None)
    section = [item for item in statements if not item.header and item.table == path]
    replaced: dict[int, str] = {}
    added: list[str] = []
    newline = _newline("".join(lines))
    for key, value in values.items():
        owners = [item for item in section if item.key == key]
        if not owners:
            added.append(f"{toml_key(key)} = {toml_value(value)}{newline}")
        elif len(owners) > 1 or owners[0].dotted or owners[0].first != owners[0].last:
            raise _UnsafeEditError(key)
        else:
            replaced[owners[0].first] = _key_line(lines[owners[0].first], value)
    anchor = section[-1].last if section else (header.last if header else -1)
    edited = [replaced.get(index, line) for index, line in enumerate(lines)]
    if added and anchor >= 0 and not edited[anchor].endswith("\n"):
        edited[anchor] += newline
    return "".join(edited[: anchor + 1] + added + edited[anchor + 1 :])


def _section_end(statements: list[_Statement], header: _Statement) -> int:
    end = header.last
    for statement in statements[statements.index(header) + 1 :]:
        if statement.header:
            break
        end = statement.last
    return end


def _line_comment(lines: list[str], statement: _Statement, index: int) -> str:
    span = lines[statement.first : statement.last + 1]
    parsed = tomllib.loads("".join(span))
    line = lines[index]
    body = line.rstrip("\r\n")
    masked = _masked(body)
    start = masked.find("#")
    while start != -1:
        trimmed = list(span)
        trimmed[index - statement.first] = body[:start] + line[len(body) :]
        try:
            if tomllib.loads("".join(trimmed)) == parsed:
                return body[start:].rstrip()
        except tomllib.TOMLDecodeError:
            pass
        start = masked.find("#", start + 1)
    return ""


def _owner(statement: _Statement, path: tuple[str, ...]) -> str:
    inner = statement.table[len(path) :]
    if inner:
        return inner[0]
    return "" if statement.header else statement.key


def _moved_comments(
    lines: list[str], statements: list[_Statement], path: tuple[str, ...], dropped: set[int]
) -> dict[str, list[str]]:
    starts = {statement.first: statement for statement in statements}
    comments: dict[str, list[str]] = {}
    pending: list[str] = []
    covered = -1
    for index in sorted(dropped):
        statement = starts.get(index)
        if statement is not None:
            rows = range(index, statement.last + 1)
            found = [comment for row in rows if (comment := _line_comment(lines, statement, row))]
            comments.setdefault(_owner(statement, path), []).extend([*pending, *found])
            pending, covered = [], statement.last
        elif index > covered and lines[index].lstrip().startswith("#"):
            pending.append(lines[index].strip())
    return comments


def _replaced_lines(statements: list[_Statement], headers: list[_Statement]) -> set[int]:
    in_statements = {
        index for statement in statements for index in range(statement.first, statement.last + 1)
    }
    start = min(header.first for header in headers)
    replaced: set[int] = set()
    for header in headers:
        first = header.first
        while first - 1 > start and first - 1 not in in_statements:
            first -= 1
        replaced.update(range(first, _section_end(statements, header) + 1))
    return replaced


def _regenerate(
    lines: list[str], statements: list[_Statement], path: tuple[str, ...], table: Mapping[str, Any]
) -> TableUpdate:
    headers = [item for item in statements if item.header and item.table[: len(path)] == path]
    dropped = _replaced_lines(statements, headers)
    comments = _moved_comments(lines, statements, path, dropped)
    newline = _newline("".join(lines))
    name = ".".join(toml_key(part) for part in path)
    block = [f"[{name}]{newline}", *(f"{comment}{newline}" for comment in comments.get("", []))]
    for key, value in table.items():
        block.extend(f"{comment}{newline}" for comment in comments.get(key, []))
        block.append(f"{toml_key(key)} = {toml_value(value)}{newline}")
    start = min(header.first for header in headers)
    kept = [line for index, line in enumerate(lines) if index not in dropped and index < start]
    rest = [line for index, line in enumerate(lines) if index not in dropped and index > start]
    moved = sum(map(len, comments.values()))
    note = (
        f"[{name}] could not be edited line by line (sub-tables, dotted keys, or a value "
        "across lines), so it was rewritten as one table of inline values"
        + (f"; its {moved} comment line(s) now sit above the key each described" if moved else "")
    )
    return TableUpdate("".join(kept + block + rest), (note,))


def _appended(text: str, path: tuple[str, ...], values: Values) -> str:
    newline = _newline(text)
    body = text if not text or text.endswith("\n") else text + newline
    block = f"[{'.'.join(toml_key(part) for part in path)}]{newline}" + "".join(
        f"{toml_key(key)} = {toml_value(value)}{newline}" for key, value in values.items()
    )
    return body + (newline if body.strip() else "") + block


def _update_toml(text: str, path: tuple[str, ...], values: Values) -> TableUpdate:
    document = _toml_document(text)
    _descend(document, path, "TOML")
    intended = _with_values(document, path, values)
    lines = _lines(text)
    statements = _statements(lines)
    if path and _defined_elsewhere(statements, path):
        raise ConfigFileError(
            f"[{'.'.join(path)}] is written as an inline or dotted key; change it by hand"
        )
    headers = [item for item in statements if item.header and item.table[: len(path)] == path]

    def in_place() -> TableUpdate:
        return TableUpdate(_edit_in_place(lines, statements, path, values))

    def appended() -> TableUpdate:
        return TableUpdate(_appended(text, path, values))

    regenerate = partial(_regenerate, lines, statements, path, _descend(intended, path, "TOML"))
    attempts: list[Callable[[], TableUpdate]] = [in_place]
    if path and not headers:
        attempts = [appended]
    elif path and any(header.table != path for header in headers):
        attempts = [regenerate]
    elif path:
        attempts = [in_place, regenerate]
    for attempt in attempts:
        try:
            result = attempt()
            if tomllib.loads(result.text) == intended:
                return result
        except (_UnsafeEditError, tomllib.TOMLDecodeError):
            continue
    raise ConfigFileError(f"cannot rewrite [{'.'.join(path)}] safely; change it by hand")


def _dotenv_parts(raw: str) -> tuple[str, str]:
    text = raw.strip()
    quoted = _DOTENV_QUOTED.match(text)
    if quoted is None:
        found = _DOTENV_COMMENT.search(text)
        start = found.start() if found is not None else len(text)
        return text[:start], text[start:]
    inner = quoted.group("inner")
    comment = quoted.group("comment")
    if quoted.group("quote") == "'":
        return inner, comment
    unescaped = re.sub(r"\\([\"\\n])", lambda match: _DOTENV_ESCAPES[match.group(1)], inner)
    return unescaped, comment


def _dotenv_assignments(text: str) -> dict[str, tuple[int, str]]:
    assignments: dict[str, tuple[int, str]] = {}
    for index, line in enumerate(_lines(text)):
        match = _DOTENV_LINE.match(line)
        if match is not None:
            assignments[match.group("key")] = (index, _dotenv_parts(line[match.end() :])[0])
    return assignments


def _dotenv_text(value: object) -> str:
    if isinstance(value, dict):
        raise ConfigFileError("a dotenv value cannot be a table")
    text = ("true" if value else "false") if isinstance(value, bool) else str(value)
    if _DOTENV_BARE.fullmatch(text):
        return text
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _update_dotenv(text: str, values: Values) -> str:
    lines = _lines(text)
    newline = _newline(text)
    assignments = _dotenv_assignments(text)
    added: list[str] = []
    for key, value in values.items():
        if key in assignments:
            index = assignments[key][0]
            line = lines[index]
            match = cast(re.Match[str], _DOTENV_LINE.match(line))
            body = line.rstrip("\r\n")
            comment = _dotenv_parts(body[match.end() :])[1]
            if comment and not comment[0].isspace():
                comment = f" {comment}"
            ending = line[len(body) :]
            lines[index] = f"{match.group('lead')}{key}={_dotenv_text(value)}{comment}{ending}"
        else:
            added.append(f"{key}={_dotenv_text(value)}{newline}")
    if added and lines and not lines[-1].endswith("\n"):
        lines[-1] += newline
    return "".join(lines + added)


def read_table(text: str, file_format: str, table: str) -> dict[str, Any]:
    if file_format == "dotenv":
        return {key: value for key, (_line, value) in _dotenv_assignments(text).items()}
    document = _json_document(text) if file_format == "json" else _toml_document(text)
    return dict(_descend(document, _table_path(table), file_format.upper()))


def update_table(text: str, file_format: str, table: str, values: Values) -> TableUpdate:
    if file_format == "dotenv":
        return TableUpdate(_update_dotenv(text, values))
    if file_format == "json":
        return TableUpdate(_update_json(text, _table_path(table), values))
    return _update_toml(text, _table_path(table), values)


def _without(document: dict[str, Any], path: tuple[str, ...]) -> None:
    node: Any = document
    for part in path[:-1]:
        node = node.get(part) if isinstance(node, dict) else None
    if isinstance(node, dict):
        node.pop(path[-1], None)


def remove_toml_keys(text: str, paths: Sequence[tuple[str, ...]]) -> str:
    intended = _toml_document(text)
    for path in paths:
        _without(intended, path)
    lines = _lines(text)
    dropped: set[int] = set()
    for statement in _statements(lines):
        owner = statement.table if statement.header else (*statement.table, statement.key)
        if any(owner[: len(path)] == path for path in paths):
            dropped.update(range(statement.first, statement.last + 1))
    result = "".join(line for index, line in enumerate(lines) if index not in dropped)
    try:
        if tomllib.loads(result) == intended:
            return result
    except tomllib.TOMLDecodeError:
        pass
    names = ", ".join(".".join(path) for path in paths)
    raise ConfigFileError(f"cannot remove {names} safely; delete them by hand")


def backup(path: Path, now: datetime | None = None) -> Path:
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%S%fZ")
    target = path.with_name(f"{path.name}.bak-{stamp}")
    shutil.copy2(path, target)
    return target


def atomic_write(path: Path, text: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(text.encode("utf-8"))
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)

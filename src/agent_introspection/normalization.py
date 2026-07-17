"""Conservative normalization for operation-level detector membership."""

from __future__ import annotations

import json
import math
import re
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath


class NormalizationError(ValueError):
    """Structured tool arguments cannot be normalized safely."""


NORMALIZATION_VERSION = 1


_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:?[0-9]{2})$",
    re.ASCII,
)
_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
_LINE_POSITION = re.compile(r"^(?P<path>.+?):\d+(?::\d+)?$")

_KNOWN_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "cargo": frozenset({"build", "check", "clippy", "fmt", "run", "test"}),
    "docker": frozenset({"build", "compose", "cp", "exec", "inspect", "logs", "ps", "run"}),
    "git": frozenset(
        {
            "add",
            "apply",
            "checkout",
            "commit",
            "diff",
            "fetch",
            "log",
            "merge",
            "mv",
            "pull",
            "push",
            "rebase",
            "restore",
            "rm",
            "show",
            "status",
            "switch",
            "worktree",
        }
    ),
    "npm": frozenset({"audit", "ci", "exec", "install", "run", "test"}),
    "pnpm": frozenset({"add", "build", "exec", "install", "run", "test"}),
    "ruff": frozenset({"check", "format", "rule", "version"}),
    "yarn": frozenset({"add", "build", "install", "run", "test"}),
}

_OPTIONS_WITH_VALUES: dict[str, frozenset[str]] = {
    "git": frozenset(
        {
            "-C",
            "-b",
            "-c",
            "-m",
            "--author",
            "--date",
            "--format",
            "--git-dir",
            "--message",
            "--pathspec-from-file",
            "--work-tree",
        }
    ),
    "grep": frozenset({"-C", "-e", "-f", "--context", "--file", "--regexp"}),
    "make": frozenset({"-C", "--directory", "-f", "--file"}),
    "pytest": frozenset(
        {
            "-k",
            "-m",
            "--confcutdir",
            "--deselect",
            "--ignore",
            "--maxfail",
            "--override-ini",
            "--rootdir",
        }
    ),
    "rg": frozenset({"-C", "-e", "-g", "-f", "--context", "--glob", "--regexp"}),
    "ruff": frozenset({"--config", "--output-file", "--output-format"}),
    "sed": frozenset({"-e", "-f", "--expression", "--file"}),
}


@dataclass(frozen=True, slots=True)
class VolatileRules:
    temporary_roots: tuple[str, ...] = ("/tmp", "/private/tmp", "/var/folders")
    volatile_keys: tuple[str, ...] = (
        "timestamp",
        "request_id",
        "call_id",
        "trace_id",
        "span_id",
        "nonce",
    )


DEFAULT_VOLATILE_RULES = VolatileRules()


@dataclass(frozen=True, slots=True)
class NormalizedOperation:
    kind: str
    executable: str
    subcommand: str | None
    target: str | None
    argv: tuple[str, ...]
    exit_code: int | None = None
    diagnostic_code: str | None = None

    @property
    def membership_key(self) -> tuple[object, ...]:
        return (self.kind, self.executable, self.subcommand, self.target, self.argv)


def _normalize_path(value: str) -> str:
    return PurePosixPath(value.replace("\\", "/")).as_posix()


def _normalize_target_token(value: str) -> str:
    if value.startswith(("http://", "https://", "file://")):
        return value
    return _normalize_path(value)


def _normalize_scalar(value: object, *, key: str | None, rules: VolatileRules) -> object:
    if key in rules.volatile_keys:
        return f"<{key}>"
    if isinstance(value, str):
        if _TIMESTAMP.fullmatch(value):
            return "<timestamp>"
        if _UUID.fullmatch(value):
            return "<generated-id>"
        for root in rules.temporary_roots:
            normalized_root = root.rstrip("/")
            if value == normalized_root or value.startswith(f"{normalized_root}/"):
                return "<temporary-path>"
        position = _LINE_POSITION.fullmatch(value)
        if position:
            return _normalize_path(position.group("path"))
        return value
    if value is None or isinstance(value, (bool, int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise NormalizationError("non-finite numeric argument is not supported")
        return value
    raise NormalizationError(f"unsupported argument value: {type(value).__name__}")


def normalize_structure(value: object, *, rules: VolatileRules = DEFAULT_VOLATILE_RULES) -> object:
    """Normalize only declared volatility while preserving semantic structure."""

    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        keys = list(value)
        if any(not isinstance(key, str) for key in keys):
            raise NormalizationError("argument object keys must be strings")
        for key in sorted(keys):
            child = value[key]
            if isinstance(child, (Mapping, list, tuple)):
                normalized[key] = normalize_structure(child, rules=rules)
            else:
                normalized[key] = _normalize_scalar(child, key=key, rules=rules)
        return normalized
    if isinstance(value, (list, tuple)):
        return [normalize_structure(item, rules=rules) for item in value]
    return _normalize_scalar(value, key=None, rules=rules)


def parse_tool_arguments(arguments: str | Mapping[str, object] | Sequence[object]) -> object:
    if not isinstance(arguments, str):
        return arguments
    try:
        decoded = json.loads(arguments, parse_constant=_reject_json_constant)
    except ValueError as exc:
        raise NormalizationError("tool arguments must be structured JSON") from exc
    if not isinstance(decoded, (dict, list)):
        raise NormalizationError("tool arguments must decode to an object or array")
    return decoded


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"unsupported JSON constant: {value}")


def _command_from_arguments(arguments: object) -> str | Sequence[str]:
    if isinstance(arguments, Mapping):
        command = arguments.get("argv", arguments.get("cmd", arguments.get("command")))
        if isinstance(command, str):
            return command
        if (
            isinstance(command, Sequence)
            and not isinstance(command, (str, bytes))
            and all(isinstance(item, str) for item in command)
        ):
            return tuple(command)
    if (
        isinstance(arguments, Sequence)
        and not isinstance(arguments, (str, bytes))
        and all(isinstance(item, str) for item in arguments)
    ):
        return tuple(arguments)
    raise NormalizationError("shell arguments require a string or string-array cmd/command")


def _positional_arguments(argv: Sequence[str], *, executable: str) -> list[str]:
    options_with_values = _OPTIONS_WITH_VALUES.get(executable, frozenset())
    positional: list[str] = []
    index = 0
    while index < len(argv):
        item = argv[index]
        if item.startswith("-") and item != "-":
            if "=" not in item and item in options_with_values:
                index += 2
                continue
            index += 1
            continue
        positional.append(item)
        index += 1
    return positional


def _operation_metadata(
    argv: Sequence[str],
) -> tuple[str, str | None, str | None]:
    executable = PurePosixPath(argv[0]).name
    positional = _positional_arguments(argv[1:], executable=executable)
    if not positional:
        return executable, None, None
    known_subcommands = _KNOWN_SUBCOMMANDS.get(executable, frozenset())
    if positional[0] in known_subcommands:
        subcommand = positional[0]
        target = _normalize_target_token(positional[-1]) if len(positional) > 1 else None
        return executable, subcommand, target
    if len(positional) == 1:
        return executable, None, _normalize_target_token(positional[0])
    return executable, positional[0], _normalize_target_token(positional[-1])


def _validate_operation_metadata(*, exit_code: int | None, diagnostic_code: str | None) -> None:
    if exit_code is not None and (isinstance(exit_code, bool) or not isinstance(exit_code, int)):
        raise NormalizationError("exit_code must be an integer or null")
    if diagnostic_code is not None and not isinstance(diagnostic_code, str):
        raise NormalizationError("diagnostic_code must be text or null")


def normalize_shell_operation(
    arguments: str | Mapping[str, object] | Sequence[object],
    *,
    exit_code: int | None = None,
    diagnostic_code: str | None = None,
    rules: VolatileRules = DEFAULT_VOLATILE_RULES,
) -> NormalizedOperation:
    _validate_operation_metadata(exit_code=exit_code, diagnostic_code=diagnostic_code)
    structured = parse_tool_arguments(arguments)
    command = _command_from_arguments(structured)
    try:
        argv = shlex.split(command, posix=True) if isinstance(command, str) else list(command)
    except ValueError as exc:
        raise NormalizationError("shell command has invalid quoting") from exc
    if not argv:
        raise NormalizationError("shell command is empty")
    normalized_argv = tuple(str(_normalize_scalar(item, key=None, rules=rules)) for item in argv)
    executable, subcommand, target = _operation_metadata(normalized_argv)
    return NormalizedOperation(
        kind="shell",
        executable=executable,
        subcommand=subcommand,
        target=target,
        argv=normalized_argv,
        exit_code=exit_code,
        diagnostic_code=diagnostic_code,
    )


def normalize_tool_operation(
    tool_name: str,
    arguments: str | Mapping[str, object] | Sequence[object],
    *,
    exit_code: int | None = None,
    diagnostic_code: str | None = None,
    rules: VolatileRules = DEFAULT_VOLATILE_RULES,
) -> NormalizedOperation:
    if not isinstance(tool_name, str) or tool_name == "":
        raise NormalizationError("tool_name must be a non-empty string")
    _validate_operation_metadata(exit_code=exit_code, diagnostic_code=diagnostic_code)
    if tool_name in {"exec", "exec_command", "shell", "bash"}:
        return normalize_shell_operation(
            arguments,
            exit_code=exit_code,
            diagnostic_code=diagnostic_code,
            rules=rules,
        )
    structured = normalize_structure(parse_tool_arguments(arguments), rules=rules)
    if not isinstance(structured, (dict, list)):
        raise NormalizationError("tool arguments must be an object or array")
    target: str | None = None
    if isinstance(structured, dict):
        for key in ("path", "file", "target", "uri"):
            candidate = structured.get(key)
            if isinstance(candidate, str):
                target = candidate if key == "uri" else _normalize_target_token(candidate)
                break
    canonical = json.dumps(structured, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return NormalizedOperation(
        kind="tool",
        executable=tool_name,
        subcommand=None,
        target=target,
        argv=(canonical,),
        exit_code=exit_code,
        diagnostic_code=diagnostic_code,
    )

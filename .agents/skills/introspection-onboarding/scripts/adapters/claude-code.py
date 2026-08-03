#!/usr/bin/env python3
"""Normalize Claude Code lifecycle hooks for the session-context runtime."""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any

EVENT_TYPES = {
    "SessionStart": "session_start",
    "SessionEnd": "session_end",
}


class InvalidHook(ValueError):
    """A hook envelope cannot be normalized authoritatively."""


def reject_constant(_: str) -> None:
    raise InvalidHook("JSON constants are not supported")


def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidHook("duplicate JSON object key")
        if key in {"hook_event_name", "session_id", "cwd"}:
            result[key] = value
    return result


def read_envelope() -> dict[str, Any]:
    try:
        envelope = json.load(
            sys.stdin,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except (json.JSONDecodeError, InvalidHook) as error:
        raise InvalidHook("input must be one unambiguous JSON object") from error

    if not isinstance(envelope, dict):
        raise InvalidHook("input must be a JSON object")
    return envelope


def require_string(envelope: dict[str, Any], name: str) -> str:
    value = envelope.get(name)
    if not isinstance(value, str) or not value:
        raise InvalidHook(f"{name} is required")
    return value


def hook_time() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def normalized_arguments() -> list[str]:
    envelope = read_envelope()
    hook_event_name = require_string(envelope, "hook_event_name")
    event_type = EVENT_TYPES.get(hook_event_name)
    if event_type is None:
        raise InvalidHook("hook_event_name is unsupported")

    session_id = require_string(envelope, "session_id")
    if "\n" in session_id or "\r" in session_id:
        raise InvalidHook("session_id must be a single line")

    workspace = require_string(envelope, "cwd")
    if not workspace.startswith("/"):
        raise InvalidHook("cwd must be an absolute path")

    return ["claude-code", session_id, event_type, hook_time(), workspace]


def main() -> int:
    try:
        arguments = normalized_arguments()
    except InvalidHook as error:
        print(f"claude-code hook rejected: {error}", file=sys.stderr)
        return 64

    runtime = Path(__file__).resolve().parent.parent / "session-context-runtime.sh"
    os.execv(str(runtime), [str(runtime), *arguments])
    return 127


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Normalize Claude Code lifecycle hooks for the session-context runtime."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

EVENT_TYPES = {
    "SessionStart": "session_start",
    "CwdChanged": "workspace_changed",
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


def require_native_timestamp(envelope: dict[str, Any]) -> str:
    if "timestamp" not in envelope:
        return hook_time()
    timestamp = envelope["timestamp"]
    if not isinstance(timestamp, str) or not timestamp:
        raise InvalidHook("timestamp must be a non-empty string")
    if any(ord(character) < 32 or ord(character) == 127 for character in timestamp):
        raise InvalidHook("timestamp must not contain control characters")
    if not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})",
        timestamp,
    ):
        raise InvalidHook("timestamp must be RFC 3339")
    try:
        dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as error:
        raise InvalidHook("timestamp must be RFC 3339") from error
    return timestamp


def require_native_value(envelope: dict[str, Any], name: str) -> str:
    value = require_string(envelope, name)
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise InvalidHook(f"{name} must not contain control characters")
    return value


def hook_time() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def normalized_arguments() -> list[str]:
    envelope = read_envelope()
    hook_event_name = require_string(envelope, "hook_event_name")
    event_type = EVENT_TYPES.get(hook_event_name)
    if event_type is None:
        raise InvalidHook("hook_event_name is unsupported")

    session_id = require_native_value(envelope, "session_id")

    workspace = require_native_value(envelope, "cwd")
    if not workspace.startswith("/"):
        raise InvalidHook("cwd must be an absolute path")

    occurred_at = require_native_timestamp(envelope)

    return ["claude-code", session_id, event_type, occurred_at, workspace]


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

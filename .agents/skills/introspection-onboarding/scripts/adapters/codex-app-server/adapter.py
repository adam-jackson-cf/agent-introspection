#!/usr/bin/env python3
"""Normalize documented Codex Desktop lifecycle hooks for session context."""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any

PRODUCER = "codex-app-server"
EVENT_TYPES = {
    "SessionStart": "session_start",
    "SessionEnd": "session_end",
}
SESSION_START_SOURCES = {"startup", "resume", "clear", "compact"}
SESSION_END_REASONS = {"other"}
AUTHORITATIVE_KEYS = {
    "hook_event_name",
    "session_id",
    "cwd",
    "source",
    "reason",
}


class InvalidHookError(ValueError):
    """A hook envelope cannot be normalized authoritatively."""


def reject_constant(_: str) -> None:
    raise InvalidHookError("JSON constants are not supported")


def reject_duplicate_authoritative_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in AUTHORITATIVE_KEYS and key in result:
            raise InvalidHookError("duplicate authoritative JSON object key")
        result[key] = value
    return result


def read_envelope() -> dict[str, Any]:
    try:
        envelope = json.load(
            sys.stdin,
            object_pairs_hook=reject_duplicate_authoritative_keys,
            parse_constant=reject_constant,
        )
    except (json.JSONDecodeError, InvalidHookError) as error:
        raise InvalidHookError("input must be one unambiguous JSON object") from error

    if not isinstance(envelope, dict):
        raise InvalidHookError("input must be a JSON object")
    return envelope


def require_string(envelope: dict[str, Any], name: str) -> str:
    value = envelope.get(name)
    if not isinstance(value, str) or not value:
        raise InvalidHookError(f"{name} is required")
    return value


def require_session_id(envelope: dict[str, Any]) -> str:
    session_id = require_string(envelope, "session_id")
    if any(ord(character) < 32 or ord(character) == 127 for character in session_id):
        raise InvalidHookError("session_id must not contain control characters")
    return session_id


def require_workspace(envelope: dict[str, Any]) -> str:
    workspace = require_string(envelope, "cwd")
    if not os.path.isabs(workspace) or not os.path.isdir(workspace):
        raise InvalidHookError("cwd must be an existing absolute directory")
    return workspace


def hook_time() -> str:
    timestamp = dt.datetime.now(dt.timezone(dt.timedelta()))
    return timestamp.isoformat(timespec="microseconds").replace("+00:00", "Z")


def normalized_arguments() -> list[str]:
    envelope = read_envelope()
    hook_event_name = require_string(envelope, "hook_event_name")
    event_type = EVENT_TYPES.get(hook_event_name)
    if event_type is None:
        raise InvalidHookError("hook_event_name is unsupported")

    if hook_event_name == "SessionStart":
        if "reason" in envelope:
            raise InvalidHookError("reason is not allowed for SessionStart")
        if require_string(envelope, "source") not in SESSION_START_SOURCES:
            raise InvalidHookError("source is unsupported for SessionStart")
    elif "source" in envelope:
        raise InvalidHookError("source is not allowed for SessionEnd")
    elif require_string(envelope, "reason") not in SESSION_END_REASONS:
        raise InvalidHookError("reason is unsupported for SessionEnd")

    session_id = require_session_id(envelope)
    workspace = require_workspace(envelope)
    return [PRODUCER, session_id, event_type, hook_time(), workspace]


def main() -> int:
    try:
        arguments = normalized_arguments()
    except InvalidHookError as error:
        print(f"codex-app-server hook rejected: {error}", file=sys.stderr)
        return 64

    runtime = Path(__file__).resolve().parent.parent.parent / "session-context-runtime.sh"
    os.execv(str(runtime), [str(runtime), *arguments])
    return 127


if __name__ == "__main__":
    raise SystemExit(main())

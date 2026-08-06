#!/usr/bin/env python3
"""Emit canonical Codex CLI lifecycle events from persistent notify callbacks."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PRODUCER = "codex-cli"
EVENT_TYPE = "agent-turn-complete"


class HookInputError(ValueError):
    """The notify envelope lacks authoritative lifecycle values."""


def _usage() -> int:
    return 64


def _reject_duplicate_authoritative_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    authoritative_keys = {"type", "thread-id", "thread.id", "thread_id", "cwd", "timestamp"}
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in authoritative_keys:
            if key in payload:
                raise HookInputError
            payload[key] = value
    return payload


def _timestamp(payload: dict[str, Any]) -> str:
    if "timestamp" not in payload:
        return datetime.now(UTC).isoformat()
    value = payload.get("timestamp")
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})",
        value,
    ):
        raise HookInputError
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise HookInputError from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HookInputError
    return parsed.isoformat()


def _envelope(argument: str) -> tuple[str, str, str]:
    try:
        payload = json.loads(argument, object_pairs_hook=_reject_duplicate_authoritative_keys)
    except json.JSONDecodeError as error:
        raise HookInputError from error
    if not isinstance(payload, dict) or payload.get("type") != EVENT_TYPE:
        raise HookInputError
    session_id = payload.get("thread-id")
    workspace = payload.get("cwd")
    if "thread.id" in payload or "thread_id" in payload:
        raise HookInputError
    if (
        not isinstance(session_id, str)
        or not session_id
        or "\n" in session_id
        or "\r" in session_id
        or not isinstance(workspace, str)
        or not workspace
        or not os.path.isabs(workspace)
        or not os.path.isdir(workspace)
    ):
        raise HookInputError
    return session_id, workspace, _timestamp(payload)


def _runtime() -> Path:
    return Path(__file__).resolve().parent.parent / "session-context-runtime.sh"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        return _usage()
    try:
        session_id, workspace, occurred_at = _envelope(argv[1])
        completed = subprocess.run(
            [str(_runtime()), PRODUCER, session_id, "session_start", occurred_at, workspace],
            check=False,
        )
        return completed.returncode
    except (HookInputError, KeyError, OSError):
        return _usage()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

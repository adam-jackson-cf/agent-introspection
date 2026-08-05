#!/usr/bin/env python3
"""Emit canonical Codex CLI lifecycle events from persistent notify callbacks."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

PRODUCER = "codex-cli"
EVENT_TYPE = "agent-turn-complete"
STATE_DIRECTORY = Path(".local/state/agent-introspection/codex-hook")


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


def _state_path(directory: Path, session_id: str) -> Path:
    return directory / f"{hashlib.sha256(session_id.encode()).hexdigest()}.json"


def _read_state(path: Path, session_id: str) -> str | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        state = json.loads(raw)
    except json.JSONDecodeError as error:
        raise HookInputError from error
    if (
        not isinstance(state, dict)
        or set(state) != {"session_id", "workspace"}
        or state.get("session_id") != session_id
        or not isinstance(state.get("workspace"), str)
    ):
        raise HookInputError
    return state["workspace"]


def _write_state(directory: Path, path: Path, session_id: str, workspace: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=".", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(
                {"session_id": session_id, "workspace": workspace},
                stream,
                separators=(",", ":"),
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary_name)
        raise


def _runtime() -> Path:
    return Path(__file__).resolve().parent.parent / "session-context-runtime.sh"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        return _usage()
    try:
        session_id, workspace, occurred_at = _envelope(argv[1])
        state_directory = Path(os.environ["HOME"]) / STATE_DIRECTORY
        state_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path = state_directory / ".lock"
        with lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            state_path = _state_path(state_directory, session_id)
            previous_workspace = _read_state(state_path, session_id)
            if previous_workspace == workspace:
                return 0
            event_type = "session_start" if previous_workspace is None else "workspace_changed"
            completed = subprocess.run(
                [str(_runtime()), PRODUCER, session_id, event_type, occurred_at, workspace],
                check=False,
            )
            if completed.returncode != 0:
                return completed.returncode
            _write_state(state_directory, state_path, session_id, workspace)
    except (HookInputError, KeyError, OSError):
        return _usage()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

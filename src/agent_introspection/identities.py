"""Canonical task, turn, project, target, and calendar identities."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")


class IdentityError(ValueError):
    """Identity evidence is invalid or insufficient."""


@dataclass(frozen=True, slots=True)
class TaskIdentity:
    kind: str
    value: str
    counts_as_distinct_task: bool

    @property
    def canonical(self) -> str:
        return f"{self.kind}:{self.value}"


def _optional_identity(value: object, *, name: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise IdentityError(f"{name} must be text or null")
    if any(character.isspace() for character in value):
        raise IdentityError(f"{name} must not contain whitespace")
    return value


@dataclass(frozen=True, slots=True)
class CanonicalActivityIdentity:
    """Describe the immutable fields that determine one canonical activity."""

    detector_id: str
    detector_version: int
    normalization_version: int
    source_ids: tuple[str, ...]
    operation_kind: str
    normalized_target: str
    normalized_failure_class: str


def canonical_activity_id(identity: CanonicalActivityIdentity) -> str:
    """Hash the attribution-independent canonical activity identity."""
    fields: tuple[object, ...] = (
        identity.detector_id,
        identity.detector_version,
        identity.normalization_version,
        *sorted(identity.source_ids),
        identity.operation_kind,
        identity.normalized_target,
        identity.normalized_failure_class,
    )
    if (
        not isinstance(identity.detector_id, str)
        or not identity.detector_id
        or isinstance(identity.detector_version, bool)
        or not isinstance(identity.detector_version, int)
        or identity.detector_version <= 0
        or isinstance(identity.normalization_version, bool)
        or not isinstance(identity.normalization_version, int)
        or identity.normalization_version <= 0
        or not isinstance(identity.operation_kind, str)
        or not identity.operation_kind
        or not isinstance(identity.normalized_target, str)
        or not isinstance(identity.normalized_failure_class, str)
        or not all(isinstance(source_id, str) and source_id for source_id in identity.source_ids)
    ):
        raise IdentityError("canonical activity identity fields are invalid")
    encoded = json.dumps(fields, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_task(
    *,
    trace_id: str,
    thread_id: str | None,
    conversation_id: str | None,
    conversation_to_thread: Mapping[str, str],
) -> TaskIdentity:
    """Resolve task precedence without promoting an episode to a canonical task."""
    resolved_thread = _optional_identity(thread_id, name="thread_id")
    resolved_conversation = _optional_identity(conversation_id, name="conversation_id")
    if resolved_thread is not None:
        return TaskIdentity("thread", resolved_thread, True)
    if resolved_conversation is not None:
        mapped = _optional_identity(
            conversation_to_thread.get(resolved_conversation),
            name="mapped thread_id",
        )
        if mapped is not None:
            return TaskIdentity("thread", mapped, True)
        return TaskIdentity("conversation", resolved_conversation, True)
    resolved_trace = _optional_identity(trace_id, name="trace_id")
    if resolved_trace is None:
        raise IdentityError("trace_id is required for episode identity")
    return TaskIdentity("episode", resolved_trace, False)


def build_conversation_thread_map(
    rows: list[tuple[str, str | None, str | None]],
) -> dict[str, str]:
    """Map conversation to thread only when shared-trace evidence is unambiguous."""
    by_conversation: dict[str, set[str]] = {}
    for trace_id, conversation_id, thread_id in rows:
        if _optional_identity(trace_id, name="trace_id") is None:
            raise IdentityError("trace_id is required for conversation mapping")
        conversation = _optional_identity(conversation_id, name="conversation_id")
        thread = _optional_identity(thread_id, name="thread_id")
        if conversation is not None and thread is not None:
            by_conversation.setdefault(conversation, set()).add(thread)
    conflicting = [
        conversation for conversation, threads in by_conversation.items() if len(threads) > 1
    ]
    if conflicting:
        raise IdentityError("conversation maps to conflicting threads")
    return {
        conversation: next(iter(threads))
        for conversation, threads in sorted(by_conversation.items())
        if len(threads) == 1
    }


def canonical_turn(*, task: TaskIdentity, turn_dot_id: str | None, turn_id: str | None) -> str:
    value = _optional_identity(turn_dot_id, name="turn.id")
    if value is None:
        value = _optional_identity(turn_id, name="turn_id")
    if value is None:
        raise IdentityError("turn.id and turn_id are both absent")
    return f"{task.canonical}/turn:{value}"


@dataclass(frozen=True, slots=True)
class ProjectIdentity:
    kind: str
    root: Path
    identity: str
    display_name: str | None = None


def canonical_git_project(root: str | Path) -> ProjectIdentity:
    """Construct the canonical Git project identity for an existing repository root."""
    try:
        normalized = Path(root).resolve(strict=True)
    except OSError as exc:
        raise IdentityError("Git project root must be an existing directory") from exc
    if not normalized.is_dir():
        raise IdentityError("Git project root must be a directory")
    identity = "git:" + hashlib.sha256(b"git\0" + normalized.as_posix().encode()).hexdigest()
    return ProjectIdentity("git", normalized, identity, normalized.name)


def normalize_target(target: str | Path, *, project_root: str | Path) -> str:
    """Return a real, project-relative POSIX target and reject scope escapes."""
    root = Path(project_root).resolve(strict=True)
    raw_target = os.fspath(target)
    if not isinstance(raw_target, str):
        raise IdentityError("target must be a path or text")
    candidate = Path(raw_target.replace("\\", "/"))
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = Path(os.path.realpath(candidate))
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise IdentityError("target resolves outside the project root") from exc
    normalized = PurePosixPath(relative.as_posix()).as_posix()
    return "." if normalized == "." else normalized


def london_day(value: datetime) -> date:
    if value.tzinfo is None:
        raise IdentityError("calendar timestamps must be timezone-aware")
    return value.astimezone(LONDON).date()

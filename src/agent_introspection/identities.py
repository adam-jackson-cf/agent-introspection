"""Canonical task, turn, project, target, and calendar identities."""

from __future__ import annotations

import hashlib
import os
import subprocess
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
    alias_source: str | None = None
    git_common_dir: Path | None = None


def _stable_project_id(kind: str, root: Path) -> str:
    digest = hashlib.sha256(f"{kind}\0{root.as_posix()}".encode()).hexdigest()
    return f"{kind}:{digest}"


def _parse_project_reference(reference: str) -> tuple[str, Path]:
    if not isinstance(reference, str):
        raise IdentityError("project aliases must be text")
    kind, separator, raw_path = reference.partition(":")
    if separator != ":" or kind not in {"git", "non_git"}:
        raise IdentityError("project aliases must use a typed absolute identity")
    path = Path(raw_path)
    if not path.is_absolute() or raw_path != os.path.normpath(raw_path):
        raise IdentityError("project aliases must contain a normalized absolute path")
    return kind, path


def _git_common_dir(cwd: Path) -> Path | None:
    completed = subprocess.run(
        ("git", "-C", os.fspath(cwd), "rev-parse", "--git-common-dir"),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    raw = completed.stdout.strip()
    if not raw:
        raise IdentityError("git returned an empty common directory")
    common = Path(raw)
    if not common.is_absolute():
        common = cwd / common
    return common.resolve(strict=True)


def discover_project(
    cwd: str | Path,
    *,
    non_git_root: str | Path | None = None,
    aliases: Mapping[str, str] | None = None,
) -> ProjectIdentity:
    """Group Git worktrees by their real common directory; apply only explicit aliases."""

    real_cwd = Path(cwd).resolve(strict=True)
    # An explicitly discovered non-Git root is authoritative. This matters when a
    # standalone project happens to live below an unrelated Git checkout.
    common = None if non_git_root is not None else _git_common_dir(real_cwd)
    if common is not None:
        if common.name != ".git":
            raise IdentityError("git common directory does not end in .git")
        kind = "git"
        root = common.parent
        git_common_dir = common
    else:
        root = Path(non_git_root if non_git_root is not None else real_cwd).resolve(strict=True)
        if real_cwd != root and root not in real_cwd.parents:
            raise IdentityError("cwd is outside the declared non-Git project root")
        git_common_dir = None
        kind = "non_git"
    source = f"{kind}:{root.as_posix()}"
    for alias_key, alias_target in (aliases or {}).items():
        source_kind, _ = _parse_project_reference(alias_key)
        target_kind, _ = _parse_project_reference(alias_target)
        if source_kind != target_kind:
            raise IdentityError("project aliases cannot change identity kind")
    canonical_source = (aliases or {}).get(source, source)
    source_alias = source if canonical_source != source else None
    canonical_kind, canonical_root = _parse_project_reference(canonical_source)
    if canonical_kind != kind:
        raise IdentityError("project aliases cannot change identity kind")
    return ProjectIdentity(
        kind=canonical_kind,
        root=canonical_root,
        identity=_stable_project_id(canonical_kind, canonical_root),
        alias_source=source_alias,
        git_common_dir=git_common_dir,
    )


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

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from lib.structure.repo_git import (
    Change,
    GitError,
    diff_changes,
    head_commit,
    index_paths,
    merge_base,
    staged_changes,
    tree_paths,
    untracked_paths,
    worktree_changes,
)
from lib.structure.settings import Settings

EVENTS = ("merge-base", "staged", "worktree")


@dataclass(frozen=True)
class Scope:
    judged: tuple[str, ...]
    base_entries: frozenset[str]
    head_paths: tuple[str, ...]
    renames: tuple[Change, ...]

    def is_new(self, segment_path: str) -> bool:
        return segment_path not in self.base_entries


def _entries(paths: Iterable[str]) -> frozenset[str]:
    entries: set[str] = set()
    for path in paths:
        parts = path.split("/")
        entries.update("/".join(parts[: index + 1]) for index in range(len(parts)))
    return frozenset(entries)


def _owned(settings: Settings, paths: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({path for path in paths if not settings.is_fixture(path)}))


def _event_changes(root: Path, settings: Settings, event: str) -> tuple[list[Change], str | None]:
    if event == "staged":
        return staged_changes(root), head_commit(root)
    if event == "worktree":
        return worktree_changes(root), head_commit(root)
    base = merge_base(root, settings.base_ref)
    return diff_changes(root, [base, "HEAD"]), base


def event_scope(root: Path, settings: Settings, event: str) -> Scope:
    changes, base = _event_changes(root, settings, event)
    head = index_paths(root) + (untracked_paths(root) if event == "worktree" else [])
    return Scope(
        judged=_owned(settings, (change.path for change in changes)),
        base_entries=_entries(tree_paths(root, base)),
        head_paths=_owned(settings, head),
        renames=tuple(
            change
            for change in changes
            if change.old_path is not None and not settings.is_fixture(change.path)
        ),
    )


def full_scope(root: Path, settings: Settings) -> Scope:
    head = _owned(settings, index_paths(root))
    return Scope(judged=head, base_entries=frozenset(), head_paths=head, renames=())


def _relative(root: Path, raw: str) -> str:
    resolved = Path(raw).resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise GitError(f"path is outside the repository: {raw}") from error


def explicit_scope(root: Path, settings: Settings, raw_paths: list[str]) -> Scope:
    head = tuple(sorted(set(index_paths(root)) | set(untracked_paths(root))))
    judged: set[str] = set()
    for raw in raw_paths:
        relative = _relative(root, raw)
        matches = [path for path in head if path == relative or path.startswith(f"{relative}/")]
        if not matches:
            raise GitError(f"path is not a tracked or untracked repository file: {raw}")
        judged.update(matches)
    return Scope(
        judged=tuple(sorted(judged)),
        base_entries=frozenset(),
        head_paths=_owned(settings, head),
        renames=(),
    )

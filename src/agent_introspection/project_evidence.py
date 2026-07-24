"""Git-validated project evidence from explicit tool workspaces."""

from __future__ import annotations

import os
import subprocess
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from agent_introspection.identities import ProjectIdentity, canonical_git_project

_GIT_TIMEOUT_SECONDS = 2.0


class ProjectEvidenceError(ValueError):
    """Tool workspace evidence is malformed or cannot establish attribution."""


@dataclass(frozen=True, slots=True)
class ToolWorkspaceInvocation:
    """One explicit workspace carried by a producer tool-invocation record."""

    log_id: str
    producer: str
    conversation_id: str
    occurred_at: datetime
    workspace: str


@dataclass(frozen=True, slots=True)
class WorkspaceResolution:
    """Resolved project state without retaining Git command diagnostics."""

    status: Literal["project", "outside_collection", "unresolved"]
    workspace: Path | None = None
    project: ProjectIdentity | None = None


@dataclass(frozen=True, slots=True)
class DirectProjectEvidence:
    """Direct project attribution for an individual tool invocation."""

    log_id: str
    producer: str
    conversation_id: str
    occurred_at: datetime
    workspace: Path
    project: ProjectIdentity


@dataclass(frozen=True, slots=True)
class ConversationProjectInterval:
    """Closed project interval bounded by direct tool-workspace evidence."""

    producer: str
    conversation_id: str
    started_at: datetime
    ended_at: datetime
    project: ProjectIdentity


@dataclass(frozen=True, slots=True)
class ProjectEvidence:
    """Direct and conversation-level project evidence derived from tool workspaces."""

    direct: tuple[DirectProjectEvidence, ...]
    intervals: tuple[ConversationProjectInterval, ...]


class WorkspaceProjectResolver(Protocol):
    """Resolve an explicit tool workspace without exposing command output."""

    def resolve(self, workspace: str) -> WorkspaceResolution:
        """Return project, neutral outside-collection, or unresolved evidence."""
        ...


class GitWorkspaceResolver:
    """Resolve existing collection workspaces through bounded Git plumbing."""

    def __init__(
        self,
        *,
        project_roots: Iterable[Path],
        git_executable: str = "git",
        timeout_seconds: float = _GIT_TIMEOUT_SECONDS,
    ) -> None:
        if not git_executable:
            raise ProjectEvidenceError("git executable is required")
        if timeout_seconds <= 0:
            raise ProjectEvidenceError("Git timeout must be positive")
        roots: list[Path] = []
        for value in project_roots:
            root = Path(value)
            if not root.is_absolute():
                raise ProjectEvidenceError("project roots must be absolute paths")
            normalized = root.resolve(strict=False)
            if normalized in roots:
                raise ProjectEvidenceError("project roots must be unique")
            roots.append(normalized)
        if not roots:
            raise ProjectEvidenceError("at least one project root is required")
        self._project_roots = tuple(roots)
        self._git_executable = git_executable
        self._timeout_seconds = timeout_seconds
        self._cache: dict[Path, WorkspaceResolution] = {}

    def _collection_root(self, workspace: Path) -> Path | None:
        for root in self._project_roots:
            try:
                workspace.relative_to(root)
            except ValueError:
                continue
            return root
        return None

    def resolve(self, workspace: str) -> WorkspaceResolution:
        """Resolve only an explicit existing directory under a collection root."""

        if not isinstance(workspace, str) or not workspace or workspace.strip() != workspace:
            return WorkspaceResolution("unresolved")
        raw_workspace = Path(workspace)
        if not raw_workspace.is_absolute():
            return WorkspaceResolution("unresolved")
        lexical_workspace = Path(os.path.abspath(raw_workspace))
        collection_root = self._collection_root(lexical_workspace)
        if collection_root is None:
            return WorkspaceResolution("outside_collection", workspace=lexical_workspace)
        try:
            resolved_workspace = raw_workspace.resolve(strict=True)
        except OSError:
            return WorkspaceResolution("unresolved", workspace=lexical_workspace)
        if (
            not resolved_workspace.is_dir()
            or self._collection_root(resolved_workspace) != collection_root
        ):
            return WorkspaceResolution("unresolved", workspace=resolved_workspace)
        cached = self._cache.get(resolved_workspace)
        if cached is not None:
            return cached
        resolution = self._resolve_collection_workspace(resolved_workspace)
        self._cache[resolved_workspace] = resolution
        return resolution

    def _resolve_collection_workspace(self, workspace: Path) -> WorkspaceResolution:
        try:
            completed = subprocess.run(
                [
                    self._git_executable,
                    "-C",
                    os.fspath(workspace),
                    "rev-parse",
                    "--show-toplevel",
                ],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=self._timeout_seconds,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
        except (OSError, subprocess.TimeoutExpired):
            return WorkspaceResolution("unresolved", workspace=workspace)
        if completed.returncode != 0:
            return WorkspaceResolution("unresolved", workspace=workspace)
        lines = completed.stdout.splitlines()
        if len(lines) != 1:
            return WorkspaceResolution("unresolved", workspace=workspace)
        reported_root = Path(lines[0])
        if not reported_root.is_absolute():
            return WorkspaceResolution("unresolved", workspace=workspace)
        try:
            project_root = reported_root.resolve(strict=True)
            workspace.relative_to(project_root)
        except (OSError, ValueError):
            return WorkspaceResolution("unresolved", workspace=workspace)
        if not project_root.is_dir() or self._collection_root(
            project_root
        ) != self._collection_root(workspace):
            return WorkspaceResolution("unresolved", workspace=workspace)
        return WorkspaceResolution(
            "project",
            workspace=workspace,
            project=canonical_git_project(project_root),
        )


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ProjectEvidenceError(f"{field} must be non-empty text")
    return value


def _group_key(invocation: ToolWorkspaceInvocation) -> tuple[str, str]:
    producer = _required_text(invocation.producer, field="producer")
    conversation_id = _required_text(invocation.conversation_id, field="conversation_id")
    _required_text(invocation.log_id, field="log_id")
    if invocation.occurred_at.tzinfo is None:
        raise ProjectEvidenceError("occurred_at must be timezone-aware")
    return producer, conversation_id


def build_project_evidence(
    invocations: Iterable[ToolWorkspaceInvocation],
    *,
    resolver: WorkspaceProjectResolver,
) -> ProjectEvidence:
    """Build direct evidence and closed intervals without promoting conflicts."""

    grouped_direct: dict[tuple[str, str], list[DirectProjectEvidence]] = defaultdict(list)
    grouped_projects: dict[tuple[str, str], dict[str, ProjectIdentity]] = defaultdict(dict)
    unresolved_groups: set[tuple[str, str]] = set()
    for invocation in invocations:
        key = _group_key(invocation)
        resolution = resolver.resolve(invocation.workspace)
        if resolution.status == "unresolved":
            unresolved_groups.add(key)
            continue
        if resolution.status == "outside_collection":
            continue
        if resolution.workspace is None or resolution.project is None:
            raise ProjectEvidenceError("project resolution must include workspace and project")
        direct = DirectProjectEvidence(
            log_id=invocation.log_id,
            producer=key[0],
            conversation_id=key[1],
            occurred_at=invocation.occurred_at,
            workspace=resolution.workspace,
            project=resolution.project,
        )
        grouped_direct[key].append(direct)
        grouped_projects[key][resolution.project.identity] = resolution.project

    direct_evidence = tuple(
        sorted(
            (item for values in grouped_direct.values() for item in values),
            key=lambda item: (item.producer, item.conversation_id, item.occurred_at, item.log_id),
        )
    )
    intervals: list[ConversationProjectInterval] = []
    for key, direct_items in grouped_direct.items():
        projects = grouped_projects[key]
        if key in unresolved_groups or len(projects) != 1:
            continue
        project = next(iter(projects.values()))
        intervals.append(
            ConversationProjectInterval(
                producer=key[0],
                conversation_id=key[1],
                started_at=min(item.occurred_at for item in direct_items),
                ended_at=max(item.occurred_at for item in direct_items),
                project=project,
            )
        )
    return ProjectEvidence(
        direct=direct_evidence,
        intervals=tuple(
            sorted(
                intervals,
                key=lambda item: (item.producer, item.conversation_id, item.started_at),
            )
        ),
    )

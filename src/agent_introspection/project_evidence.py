"""Git-validated project evidence from explicit tool workspaces."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol

from agent_introspection.identities import ProjectIdentity, canonical_git_project
from agent_introspection.source import ProjectEvidenceRow

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


MAX_LEGACY_PROJECT_ATTRIBUTION_RANGE = timedelta(days=31)
_CODEX_PRODUCERS = frozenset({"codex-cli", "codex-app-server"})


@dataclass(frozen=True, slots=True)
class LegacyProjectAttributionResult:
    """Verified result of one bounded legacy project-attribution application."""

    fact_set_id: str
    accepted: int
    rejected: int
    unresolved: int
    denominator: int
    activity_ids: tuple[str, ...]
    outbox_event_ids: tuple[str, ...]


def _legacy_required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ProjectEvidenceError(f"{field} must be non-empty text")
    return value


def _legacy_timestamp_ns(value: object, *, start_ns: int, end_ns: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not start_ns < value <= end_ns:
        raise ProjectEvidenceError("source timestamp is outside the requested range")
    return value


def _legacy_fact_set_id(
    *,
    start_ns: int,
    end_ns: int,
    approved_by: str,
    accepted: tuple[DirectProjectEvidence, ...],
) -> str:
    payload = {
        "approved_by": approved_by,
        "accepted": [
            {
                "conversation_id": item.conversation_id,
                "log_id": item.log_id,
                "occurred_at_ns": int(item.occurred_at.timestamp() * 1_000_000_000),
                "producer": item.producer,
                "project_id": item.project.identity,
                "workspace": item.workspace.as_posix(),
            }
            for item in accepted
        ],
        "end_ns": end_ns,
        "start_ns": start_ns,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _persist_legacy_project(
    connection: sqlite3.Connection, project: ProjectIdentity, *, created_at: str
) -> None:
    connection.execute(
        """
        INSERT INTO project_identities (
            id, identity_kind, canonical_path, git_common_dir, created_at, canonical_name
        ) VALUES (?, ?, ?, NULL, ?, ?)
        ON CONFLICT(id) DO NOTHING
        """,
        (
            project.identity,
            project.kind,
            project.root.as_posix(),
            created_at,
            project.display_name,
        ),
    )


def apply_legacy_project_attribution(
    connection: sqlite3.Connection,
    *,
    project_roots: Iterable[Path],
    source_rows: Iterable[ProjectEvidenceRow],
    start: datetime,
    end: datetime,
    approved_by: str,
) -> LegacyProjectAttributionResult:
    """Apply one bounded, allowlisted legacy project-attribution fact set exactly once."""

    approved_by = _legacy_required_text(approved_by, field="approved_by")
    if start.tzinfo is None or end.tzinfo is None:
        raise ProjectEvidenceError("legacy attribution bounds must be timezone-aware")
    start = start.astimezone(UTC)
    end = end.astimezone(UTC)
    if start >= end or end - start > MAX_LEGACY_PROJECT_ATTRIBUTION_RANGE:
        raise ProjectEvidenceError("legacy attribution range must be ordered and at most 31 days")
    start_ns = int(start.timestamp() * 1_000_000_000)
    end_ns = int(end.timestamp() * 1_000_000_000)
    resolver = GitWorkspaceResolver(project_roots=project_roots)
    accepted: list[DirectProjectEvidence] = []
    rejected = 0
    unresolved = 0
    denominator = 0
    source_log_ids: set[str] = set()
    for row in source_rows:
        denominator += 1
        try:
            producer = _legacy_required_text(row.producer, field="producer")
            conversation_id = _legacy_required_text(row.conversation_id, field="conversation_id")
            log_id = _legacy_required_text(row.log_id, field="log_id")
            timestamp_ns = _legacy_timestamp_ns(row.timestamp_ns, start_ns=start_ns, end_ns=end_ns)
            workspace = _legacy_required_text(row.tool_workspace, field="tool_workspace")
        except (AttributeError, ProjectEvidenceError):
            rejected += 1
            continue
        if producer not in _CODEX_PRODUCERS:
            rejected += 1
            continue
        if log_id in source_log_ids:
            rejected += 1
            continue
        source_log_ids.add(log_id)
        resolution = resolver.resolve(workspace)
        if resolution.status == "outside_collection":
            rejected += 1
            continue
        if (
            resolution.status != "project"
            or resolution.workspace is None
            or resolution.project is None
        ):
            unresolved += 1
            continue
        accepted.append(
            DirectProjectEvidence(
                log_id=log_id,
                producer=producer,
                conversation_id=conversation_id,
                occurred_at=datetime.fromtimestamp(timestamp_ns / 1_000_000_000, tz=UTC),
                workspace=resolution.workspace,
                project=resolution.project,
            )
        )
    facts = tuple(
        sorted(
            accepted,
            key=lambda item: (
                item.producer,
                item.conversation_id,
                item.occurred_at,
                item.log_id,
            ),
        )
    )
    fact_set_id = _legacy_fact_set_id(
        start_ns=start_ns,
        end_ns=end_ns,
        approved_by=approved_by,
        accepted=facts,
    )
    created_at = end.isoformat()
    if denominator != len(facts) + rejected + unresolved:
        raise RuntimeError("legacy project-attribution denominator is not conserved")
    from agent_introspection.database import (
        CanonicalActivity,
        CanonicalAttribution,
        CanonicalSourceMembership,
        persist_canonical_activity,
    )
    from agent_introspection.telemetry import (
        CanonicalActivityVersionEvent,
        enqueue_canonical_activity_version,
    )

    connection.execute("BEGIN IMMEDIATE")
    try:
        if (
            connection.execute(
                "SELECT 1 FROM attribution_reanalysis_fact_sets WHERE id = ?", (fact_set_id,)
            ).fetchone()
            is not None
        ):
            raise RuntimeError("legacy project-attribution fact set was already applied")
        semantic_hash = hashlib.sha256(fact_set_id.encode("ascii")).hexdigest()
        connection.execute(
            """
            INSERT INTO attribution_reanalysis_fact_sets (
                id, window_start_ns, window_end_ns, source_contract_fingerprint,
                semantic_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (fact_set_id, start_ns, end_ns, semantic_hash, semantic_hash, created_at),
        )
        activity_ids: list[str] = []
        outbox_event_ids: list[str] = []
        for item in facts:
            _persist_legacy_project(connection, item.project, created_at=created_at)
            timestamp_ns = int(item.occurred_at.timestamp() * 1_000_000_000)
            activity = CanonicalActivity(
                producer=item.producer,
                producer_surface=item.producer,
                correlation_id=item.conversation_id,
                source_started_at_ns=timestamp_ns,
                source_ended_at_ns=timestamp_ns,
                detector_id="project-attribution",
                detector_version=1,
                normalization_version=1,
                source_membership=CanonicalSourceMembership(
                    event_ids=(item.log_id,), log_ids=(item.log_id,), span_ids=()
                ),
                operation_kind="tool.workspace",
                target_kind="git_workspace",
                normalized_target=item.project.root.as_posix(),
                normalized_failure_class="",
                created_at=created_at,
            )
            attribution = CanonicalAttribution(
                state="resolved",
                project_identity_id=item.project.identity,
                method="legacy_project_attribution",
                evidence_id=item.log_id,
                reason_code=None,
                created_at=created_at,
            )
            write = persist_canonical_activity(connection, activity, attribution)
            if not write.version_inserted:
                raise RuntimeError("legacy project-attribution canonical activity already exists")
            activity_ids.append(write.activity_id)
            outbox_event_ids.append(
                enqueue_canonical_activity_version(
                    connection,
                    CanonicalActivityVersionEvent(
                        activity_id=write.activity_id,
                        version=write.version,
                        timestamp_ns=timestamp_ns,
                        attributes={
                            "activity.attribution.method": attribution.method,
                            "activity.attribution.project_identity_id": item.project.identity,
                            "activity.attribution.state": attribution.state,
                        },
                    ),
                )
            )
        if len(set(activity_ids)) != len(activity_ids) or len(set(outbox_event_ids)) != len(
            outbox_event_ids
        ):
            raise RuntimeError("legacy project-attribution source provenance is not unique")
        if outbox_event_ids:
            rows = connection.execute(
                "SELECT event_id FROM otlp_outbox WHERE event_id IN ({})".format(
                    ",".join("?" for _ in outbox_event_ids)
                ),
                tuple(outbox_event_ids),
            ).fetchall()
            if {str(row[0]) for row in rows} != set(outbox_event_ids):
                raise RuntimeError("legacy project-attribution outbox verification failed")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return LegacyProjectAttributionResult(
        fact_set_id=fact_set_id,
        accepted=len(facts),
        rejected=rejected,
        unresolved=unresolved,
        denominator=denominator,
        activity_ids=tuple(sorted(activity_ids)),
        outbox_event_ids=tuple(sorted(outbox_event_ids)),
    )

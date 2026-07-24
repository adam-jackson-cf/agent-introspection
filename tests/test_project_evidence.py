from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import agent_introspection.project_evidence as project_evidence
from agent_introspection.project_evidence import (
    GitWorkspaceResolver,
    ToolWorkspaceInvocation,
    build_project_evidence,
)


def _project(collection: Path, name: str) -> Path:
    project = collection / name
    project.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", project], check=True)
    return project


def _invocation(
    *,
    log_id: str,
    workspace: Path | str,
    occurred_at: datetime,
    conversation_id: str = "conversation-1",
) -> ToolWorkspaceInvocation:
    return ToolWorkspaceInvocation(
        log_id=log_id,
        producer="omp",
        conversation_id=conversation_id,
        occurred_at=occurred_at,
        workspace=str(workspace),
    )


def test_resolver_accepts_a_nested_workspace_and_caches_git_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    collection = tmp_path / "Projects"
    project = _project(collection, "project")
    workspace = project / "src" / "nested"
    workspace.mkdir(parents=True)
    resolver = GitWorkspaceResolver(project_roots=(collection,))
    calls: list[list[str]] = []
    original_run = project_evidence.subprocess.run

    def capture(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return original_run(argv, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(project_evidence.subprocess, "run", capture)

    first = resolver.resolve(str(workspace))
    second = resolver.resolve(str(workspace))

    assert first == second
    assert first.project is not None
    assert first.project.root == project
    assert calls == [["git", "-C", str(workspace), "rev-parse", "--show-toplevel"]]


def test_outside_collection_workspaces_are_neutral_without_git_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    collection = tmp_path / "Projects"
    project = _project(collection, "project")
    outside = tmp_path / "outside-missing"
    resolver = GitWorkspaceResolver(project_roots=(collection,))
    calls: list[list[str]] = []
    original_run = project_evidence.subprocess.run

    def capture(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return original_run(argv, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(project_evidence.subprocess, "run", capture)
    now = datetime(2026, 7, 24, 12, tzinfo=UTC)

    evidence = build_project_evidence(
        (
            _invocation(log_id="project", workspace=project, occurred_at=now),
            _invocation(
                log_id="outside",
                workspace=outside,
                occurred_at=now + timedelta(seconds=1),
            ),
        ),
        resolver=resolver,
    )

    assert calls == [["git", "-C", str(project), "rev-parse", "--show-toplevel"]]
    assert [item.log_id for item in evidence.direct] == ["project"]
    assert len(evidence.intervals) == 1


def test_relative_and_missing_workspaces_are_unresolved_and_block_intervals(tmp_path: Path) -> None:
    collection = tmp_path / "Projects"
    project = _project(collection, "project")
    resolver = GitWorkspaceResolver(project_roots=(collection,))
    start = datetime(2026, 7, 24, 12, tzinfo=UTC)

    evidence = build_project_evidence(
        (
            _invocation(log_id="valid", workspace=project, occurred_at=start),
            _invocation(log_id="relative", workspace="relative/project", occurred_at=start),
            _invocation(
                log_id="missing",
                workspace=collection / "missing",
                occurred_at=start + timedelta(seconds=1),
            ),
        ),
        resolver=resolver,
    )

    assert resolver.resolve("relative/project").status == "unresolved"
    assert resolver.resolve(str(collection / "missing")).status == "unresolved"
    assert [item.log_id for item in evidence.direct] == ["valid"]
    assert evidence.intervals == ()


def test_unresolved_collection_workspace_preserves_direct_evidence_but_blocks_interval(
    tmp_path: Path,
) -> None:
    collection = tmp_path / "Projects"
    project = _project(collection, "project")
    unresolved = collection / "not-a-repository"
    unresolved.mkdir()
    resolver = GitWorkspaceResolver(project_roots=(collection,))
    now = datetime(2026, 7, 24, 12, tzinfo=UTC)

    evidence = build_project_evidence(
        (
            _invocation(log_id="direct", workspace=project, occurred_at=now),
            _invocation(log_id="unresolved", workspace=unresolved, occurred_at=now),
        ),
        resolver=resolver,
    )

    assert [item.log_id for item in evidence.direct] == ["direct"]
    assert evidence.intervals == ()


def test_multi_project_conversation_preserves_direct_evidence_without_interval(
    tmp_path: Path,
) -> None:
    collection = tmp_path / "Projects"
    first = _project(collection, "first")
    second = _project(collection, "second")
    resolver = GitWorkspaceResolver(project_roots=(collection,))
    now = datetime(2026, 7, 24, 12, tzinfo=UTC)

    evidence = build_project_evidence(
        (
            _invocation(log_id="first", workspace=first, occurred_at=now),
            _invocation(log_id="second", workspace=second, occurred_at=now + timedelta(seconds=1)),
        ),
        resolver=resolver,
    )

    assert {item.project.root for item in evidence.direct} == {first, second}
    assert evidence.intervals == ()


def test_interval_is_closed_and_includes_its_first_and_last_direct_evidence(tmp_path: Path) -> None:
    collection = tmp_path / "Projects"
    project = _project(collection, "project")
    resolver = GitWorkspaceResolver(project_roots=(collection,))
    first = datetime(2026, 7, 24, 12, tzinfo=UTC)
    last = first + timedelta(seconds=30)

    evidence = build_project_evidence(
        (
            _invocation(log_id="last", workspace=project, occurred_at=last),
            _invocation(log_id="first", workspace=project, occurred_at=first),
        ),
        resolver=resolver,
    )

    assert len(evidence.intervals) == 1
    interval = evidence.intervals[0]
    assert interval.started_at == first
    assert interval.ended_at == last
    assert {item.occurred_at for item in evidence.direct} == {
        interval.started_at,
        interval.ended_at,
    }


def test_git_command_failure_is_unresolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    collection = tmp_path / "Projects"
    workspace = collection / "project"
    workspace.mkdir(parents=True)
    resolver = GitWorkspaceResolver(project_roots=(collection,))

    def failure(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, "", "")

    monkeypatch.setattr(project_evidence.subprocess, "run", failure)

    assert resolver.resolve(str(workspace)).status == "unresolved"

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import agent_introspection.project_evidence as project_evidence
from agent_introspection.database import connect_database
from agent_introspection.project_evidence import (
    GitWorkspaceResolver,
    ToolWorkspaceInvocation,
    apply_legacy_project_attribution,
    build_project_evidence,
)
from agent_introspection.source import ProjectEvidenceRow


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


def test_legacy_project_attribution_writes_one_canonical_fact_set_and_refuses_reapplication(
    tmp_path: Path,
) -> None:
    collection = tmp_path / "Projects"
    project = _project(collection, "project")
    connection = connect_database(tmp_path / "introspection.sqlite3")
    start = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)
    unresolved_workspace = collection / "unresolved"
    unresolved_workspace.mkdir()
    rows = (
        ProjectEvidenceRow(
            timestamp_ns=int((start + timedelta(minutes=1)).timestamp() * 1_000_000_000),
            log_id="log-1",
            trace_id="trace-1",
            producer="codex-cli",
            conversation_id="conversation-1",
            tool_workspace=str(project),
        ),
        ProjectEvidenceRow(
            timestamp_ns=int((start + timedelta(minutes=2)).timestamp() * 1_000_000_000),
            log_id="log-2",
            trace_id="trace-2",
            producer="omp",
            conversation_id="conversation-2",
            tool_workspace=str(project),
        ),
        ProjectEvidenceRow(
            timestamp_ns=int((start + timedelta(minutes=3)).timestamp() * 1_000_000_000),
            log_id="log-3",
            trace_id="trace-3",
            producer="codex-cli",
            conversation_id="conversation-3",
            tool_workspace=str(unresolved_workspace),
        ),
    )
    try:
        result = apply_legacy_project_attribution(
            connection,
            project_roots=(collection,),
            source_rows=rows,
            start=start,
            end=end,
            approved_by="operator",
        )
        assert (result.accepted, result.rejected, result.unresolved, result.denominator) == (
            1,
            1,
            1,
            3,
        )
        assert result.denominator == result.accepted + result.rejected + result.unresolved
        assert len(result.activity_ids) == len(result.outbox_event_ids) == 1
        assert connection.execute("SELECT COUNT(*) FROM canonical_activities").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT event_id FROM otlp_outbox WHERE event_id = ?", (result.outbox_event_ids[0],)
            ).fetchone()[0]
            == result.outbox_event_ids[0]
        )
        with pytest.raises(RuntimeError, match="already applied"):
            apply_legacy_project_attribution(
                connection,
                project_roots=(collection,),
                source_rows=rows,
                start=start,
                end=end,
                approved_by="operator",
            )
    finally:
        connection.close()


def test_legacy_project_attribution_rejects_invalid_and_outside_workspaces(
    tmp_path: Path,
) -> None:
    collection = tmp_path / "Projects"
    project = _project(collection, "project")
    connection = connect_database(tmp_path / "introspection.sqlite3")
    start = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)
    rows = (
        ProjectEvidenceRow(
            timestamp_ns=int((start + timedelta(minutes=1)).timestamp() * 1_000_000_000),
            log_id="accepted",
            trace_id="trace-accepted",
            producer="codex-cli",
            conversation_id="conversation-accepted",
            tool_workspace=str(project),
        ),
        ProjectEvidenceRow(
            timestamp_ns=int((start + timedelta(minutes=2)).timestamp() * 1_000_000_000),
            log_id="invalid",
            trace_id="trace-invalid",
            producer="codex-cli",
            conversation_id="conversation-invalid",
            tool_workspace="",
        ),
        ProjectEvidenceRow(
            timestamp_ns=int((start + timedelta(minutes=3)).timestamp() * 1_000_000_000),
            log_id="outside",
            trace_id="trace-outside",
            producer="codex-cli",
            conversation_id="conversation-outside",
            tool_workspace=str(tmp_path / "outside"),
        ),
    )
    try:
        result = apply_legacy_project_attribution(
            connection,
            project_roots=(collection,),
            source_rows=rows,
            start=start,
            end=end,
            approved_by="operator",
        )
        assert (result.accepted, result.rejected, result.unresolved, result.denominator) == (
            1,
            2,
            0,
            3,
        )
        assert result.denominator == result.accepted + result.rejected + result.unresolved
    finally:
        connection.close()

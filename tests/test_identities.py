from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_introspection.identities import (
    IdentityError,
    build_conversation_thread_map,
    canonical_task,
    canonical_turn,
    discover_project,
    london_day,
    normalize_target,
)


def _git(*args: str, cwd: Path) -> None:
    local_variables = subprocess.run(
        ("git", "rev-parse", "--local-env-vars"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    environment = os.environ.copy()
    for variable in local_variables:
        environment.pop(variable, None)
    subprocess.run(
        ("git", *args),
        cwd=cwd,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def test_task_precedence_mapping_and_episode_threshold_eligibility() -> None:
    mapping = build_conversation_thread_map(
        [("trace-a", "conversation-a", "thread-a"), ("trace-a", "conversation-b", "thread-a")]
    )
    task = canonical_task(
        trace_id="trace-b",
        thread_id=None,
        conversation_id="conversation-a",
        conversation_to_thread=mapping,
    )
    assert task.canonical == "thread:thread-a"
    assert canonical_turn(task=task, turn_dot_id="dot", turn_id="plain").endswith("/turn:dot")
    conversation = canonical_task(
        trace_id="trace-b",
        thread_id=None,
        conversation_id="unmapped",
        conversation_to_thread=mapping,
    )
    assert conversation.canonical == "conversation:unmapped"
    episode = canonical_task(
        trace_id="trace-only",
        thread_id=None,
        conversation_id=None,
        conversation_to_thread=mapping,
    )
    assert episode.canonical == "episode:trace-only"
    assert episode.counts_as_distinct_task is False
    assert (
        canonical_task(
            trace_id="trace-b",
            thread_id="thread-explicit",
            conversation_id="conversation-a",
            conversation_to_thread=mapping,
        ).canonical
        == "thread:thread-explicit"
    )
    assert canonical_turn(task=conversation, turn_dot_id="", turn_id="plain") == (
        "conversation:unmapped/turn:plain"
    )


def test_conversation_mapping_rejects_conflicting_thread_evidence() -> None:
    with pytest.raises(IdentityError, match="conflicting"):
        build_conversation_thread_map(
            [("trace-a", "conversation", "thread-a"), ("trace-b", "conversation", "thread-b")]
        )


def test_conversation_mapping_does_not_promote_ambiguous_trace_evidence() -> None:
    with pytest.raises(IdentityError, match="conflicting"):
        build_conversation_thread_map(
            [
                ("trace-a", "conversation", "thread-a"),
                ("trace-a", "conversation", "thread-b"),
                ("trace-b", "conversation", "thread-a"),
            ]
        )


def test_git_worktrees_share_the_common_project_identity(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git("init", "-b", "main", cwd=repository)
    _git("config", "user.email", "test@example.invalid", cwd=repository)
    _git("config", "user.name", "Test", cwd=repository)
    (repository / "tracked.txt").write_text("tracked\n")
    _git("add", "tracked.txt", cwd=repository)
    _git("commit", "-m", "initial", cwd=repository)
    worktree = tmp_path / "worktree"
    _git("worktree", "add", "-b", "branch", str(worktree), cwd=repository)
    assert discover_project(repository).identity == discover_project(worktree).identity
    assert discover_project(worktree).root == repository.resolve()


def test_realpath_targets_and_explicit_project_aliases(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "module.py").write_text("pass\n")
    link = root / "linked.py"
    link.symlink_to(root / "src" / "module.py")
    assert normalize_target(link, project_root=root) == "src/module.py"
    with pytest.raises(IdentityError, match="outside"):
        normalize_target(tmp_path / "elsewhere.py", project_root=root)
    source = f"non_git:{root.resolve().as_posix()}"
    prior = tmp_path / "prior-name"
    prior.mkdir()
    aliased = discover_project(root, non_git_root=root, aliases={source: f"non_git:{prior}"})
    assert aliased.root == prior
    assert aliased.alias_source == source
    assert aliased.kind == "non_git"
    assert (
        discover_project(root, non_git_root=root).identity
        != discover_project(prior, non_git_root=prior).identity
    )
    assert normalize_target("src\\module.py", project_root=root) == "src/module.py"
    assert normalize_target(root, project_root=root) == "."
    with pytest.raises(IdentityError, match="normalized"):
        discover_project(
            root,
            non_git_root=root,
            aliases={source: f"non_git:{prior}/../prior-name"},
        )
    with pytest.raises(IdentityError, match="identity kind"):
        discover_project(
            root,
            non_git_root=root,
            aliases={source: f"git:{prior}"},
        )


def test_london_calendar_days_follow_both_dst_boundaries() -> None:
    assert london_day(datetime(2026, 3, 29, 0, 30, tzinfo=UTC)).isoformat() == "2026-03-29"
    assert london_day(datetime(2026, 3, 29, 23, 30, tzinfo=UTC)).isoformat() == "2026-03-30"
    assert london_day(datetime(2026, 10, 25, 0, 30, tzinfo=UTC)).isoformat() == "2026-10-25"
    assert london_day(datetime(2026, 10, 25, 23, 30, tzinfo=UTC)).isoformat() == "2026-10-25"
    with pytest.raises(IdentityError, match="timezone-aware"):
        london_day(datetime(2026, 1, 1))

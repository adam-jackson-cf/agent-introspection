from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_introspection.identities import (
    IdentityError,
    build_conversation_thread_map,
    canonical_task,
    canonical_turn,
    london_day,
    normalize_target,
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


def test_normalize_target_resolves_links_and_rejects_scope_escapes(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "module.py").write_text("pass\n")
    link = root / "linked.py"
    link.symlink_to(root / "src" / "module.py")
    assert normalize_target(link, project_root=root) == "src/module.py"
    with pytest.raises(IdentityError, match="outside"):
        normalize_target(tmp_path / "elsewhere.py", project_root=root)
    assert normalize_target("src\\module.py", project_root=root) == "src/module.py"
    assert normalize_target(root, project_root=root) == "."


def test_london_calendar_days_follow_both_dst_boundaries() -> None:
    assert london_day(datetime(2026, 3, 29, 0, 30, tzinfo=UTC)).isoformat() == "2026-03-29"
    assert london_day(datetime(2026, 3, 29, 23, 30, tzinfo=UTC)).isoformat() == "2026-03-30"
    assert london_day(datetime(2026, 10, 25, 0, 30, tzinfo=UTC)).isoformat() == "2026-10-25"
    assert london_day(datetime(2026, 10, 25, 23, 30, tzinfo=UTC)).isoformat() == "2026-10-25"
    with pytest.raises(IdentityError, match="timezone-aware"):
        london_day(datetime(2026, 1, 1))

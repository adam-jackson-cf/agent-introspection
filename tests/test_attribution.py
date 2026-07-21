from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_introspection.attribution import (
    Attribution,
    direct_trace_attribution,
    persist_thread_evidence,
    resolve_attribution,
)
from agent_introspection.database import connect_database
from agent_introspection.source import TraceRow


def _trace(*, thread_id: str | None, cwd: str | None) -> TraceRow:
    return TraceRow(
        trace_id="trace-1",
        turn_id=None,
        thread_id=thread_id,
        cwd=cwd,
        started_at=datetime(2026, 7, 20, tzinfo=UTC),
        ended_at=datetime(2026, 7, 20, 1, tzinfo=UTC),
        total_tokens=1,
        tool_calls=0,
    )


def test_direct_cwd_precedes_thread_evidence_and_invalid_paths_are_unresolved(
    tmp_path: Path,
) -> None:
    connection = connect_database(tmp_path / "introspection.sqlite3")
    try:
        direct = direct_trace_attribution(_trace(thread_id="thread", cwd=str(tmp_path)))
        assert direct.method == "trace_cwd"
        assert direct.project_id is not None
        assert direct_trace_attribution(
            _trace(thread_id="thread", cwd="/not/a/project")
        ) == Attribution(None, "unresolved")
    finally:
        connection.close()


def test_thread_evidence_requires_one_project_in_the_requested_window(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "introspection.sqlite3")
    try:
        project_one = tmp_path / "one"
        project_one.mkdir()
        trace_one = _trace(thread_id="thread", cwd=str(project_one))
        direct_one = direct_trace_attribution(trace_one)
        assert direct_one.project is not None
        connection.execute(
            """
            INSERT INTO project_identities (
                id, identity_kind, canonical_path, git_common_dir, created_at
            )
            VALUES (?, 'non_git', ?, NULL, 'now')
            """,
            (direct_one.project.identity, direct_one.project.root.as_posix()),
        )
        persist_thread_evidence(
            connection,
            trace=trace_one,
            attribution=direct_one,
            source_contract_fingerprint="a" * 64,
            created_at="now",
        )
        connection.commit()
        resolved = resolve_attribution(
            connection,
            trace=None,
            thread_id="thread",
            conversation_thread_id=None,
            start_ns=0,
            end_ns=2_000_000_000_000_000_000,
        )
        assert resolved == Attribution(direct_one.project.identity, "thread_cwd")

        project_two = tmp_path / "two"
        project_two.mkdir()
        trace_two = _trace(thread_id="thread", cwd=str(project_two))
        direct_two = direct_trace_attribution(trace_two)
        assert direct_two.project is not None
        connection.execute(
            """
            INSERT INTO project_identities (
                id, identity_kind, canonical_path, git_common_dir, created_at
            )
            VALUES (?, 'non_git', ?, NULL, 'now')
            """,
            (direct_two.project.identity, direct_two.project.root.as_posix()),
        )
        persist_thread_evidence(
            connection,
            trace=trace_two,
            attribution=direct_two,
            source_contract_fingerprint="b" * 64,
            created_at="now",
        )
        connection.commit()
        assert (
            resolve_attribution(
                connection,
                trace=None,
                thread_id="thread",
                conversation_thread_id=None,
                start_ns=0,
                end_ns=2_000_000_000_000_000_000,
            ).method
            == "unresolved"
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE thread_project_evidence SET thread_id = 'other'")
    finally:
        connection.close()


def test_conversation_thread_evidence_is_used_only_when_it_maps_to_one_project(
    tmp_path: Path,
) -> None:
    connection = connect_database(tmp_path / "introspection.sqlite3")
    try:
        project = tmp_path / "project"
        project.mkdir()
        trace = _trace(thread_id="thread", cwd=str(project))
        direct = direct_trace_attribution(trace)
        assert direct.project is not None
        connection.execute(
            """
            INSERT INTO project_identities (
                id, identity_kind, canonical_path, git_common_dir, created_at
            )
            VALUES (?, 'non_git', ?, NULL, 'now')
            """,
            (direct.project.identity, direct.project.root.as_posix()),
        )
        persist_thread_evidence(
            connection,
            trace=trace,
            attribution=direct,
            source_contract_fingerprint="a" * 64,
            created_at="now",
        )
        connection.commit()
        resolved = resolve_attribution(
            connection,
            trace=None,
            thread_id=None,
            conversation_thread_id="thread",
            start_ns=0,
            end_ns=2_000_000_000_000_000_000,
        )
        assert resolved == Attribution(direct.project.identity, "conversation_thread_cwd")
        assert resolve_attribution(
            connection,
            trace=None,
            thread_id=None,
            conversation_thread_id="unrelated-project-name",
            start_ns=0,
            end_ns=2_000_000_000_000_000_000,
        ) == Attribution(None, "unresolved")
    finally:
        connection.close()

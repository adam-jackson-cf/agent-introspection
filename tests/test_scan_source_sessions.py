from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_introspection.database import connect_database
from agent_introspection.scan import (
    _advance_raw_source_watermark,
    _persist_source_sessions,
    _reconcile_late_source_sessions,
    _source_session_terminal,
)
from agent_introspection.session_context import drain_inbox, parse_event, spool_event
from agent_introspection.source import CANONICAL_SERVICE_PRODUCERS, SourceSessionRow


def _source_session_group_id(native_ids: tuple[str, ...]) -> str:
    import hashlib
    import json

    return hashlib.sha256(json.dumps(native_ids, separators=(",", ":")).encode()).hexdigest()


def _row(source_id: str, thread_ids: tuple[str, ...]) -> SourceSessionRow:
    return SourceSessionRow(
        source_kind="log",
        source_id=source_id,
        source_timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        service_name="codex_exec",
        session_ids=(),
        thread_ids=thread_ids,
        legacy_thread_ids=(),
        gen_ai_conversation_ids=(),
    )


def test_source_session_resolution_uses_canonical_service_mapping(
    tmp_path: Path,
) -> None:
    connection = connect_database(tmp_path / "introspection.sqlite3")
    canonical = _row("canonical", ())
    assert CANONICAL_SERVICE_PRODUCERS[canonical.service_name] == ("codex-cli", "codex-cli")
    assert _source_session_terminal(connection, canonical)[:2] == (
        "failed",
        "missing_native_session_id",
    )

    unrelated = SourceSessionRow(
        source_kind=canonical.source_kind,
        source_id="unrelated",
        source_timestamp=canonical.source_timestamp,
        service_name="agent-introspection",
        session_ids=canonical.session_ids,
        thread_ids=canonical.thread_ids,
        legacy_thread_ids=canonical.legacy_thread_ids,
        gen_ai_conversation_ids=canonical.gen_ai_conversation_ids,
    )
    assert _source_session_terminal(connection, unrelated)[:2] == (
        "failed",
        "unmapped_service_name",
    )


@pytest.mark.parametrize(
    ("row", "native_ids"),
    [
        (
            SourceSessionRow(
                "log",
                "claude",
                datetime(2026, 1, 1, tzinfo=UTC),
                "claude-code",
                ("claude-session",),
                ("wrong-thread",),
                (),
                ("wrong-conversation",),
            ),
            ("claude-session",),
        ),
        (
            SourceSessionRow(
                "log",
                "codex-cli",
                datetime(2026, 1, 1, tzinfo=UTC),
                "codex-cli",
                ("wrong-session",),
                ("thread-id",),
                (),
                ("wrong-conversation",),
            ),
            ("thread-id",),
        ),
        (
            SourceSessionRow(
                "trace",
                "codex-app",
                datetime(2026, 1, 1, tzinfo=UTC),
                "codex-app-server",
                ("wrong-session",),
                (),
                ("thread_id",),
                ("wrong-conversation",),
            ),
            ("thread_id",),
        ),
        (
            SourceSessionRow(
                "trace",
                "omp",
                datetime(2026, 1, 1, tzinfo=UTC),
                "omp",
                ("wrong-session",),
                ("wrong-thread",),
                (),
                ("conversation-id",),
            ),
            ("conversation-id",),
        ),
    ],
)
def test_native_ids_follow_exact_signal_and_service_contract(
    tmp_path: Path, row: SourceSessionRow, native_ids: tuple[str, ...]
) -> None:
    connection = connect_database(tmp_path / "introspection.sqlite3")
    assert row.native_session_ids == native_ids
    assert row.session_status == "exact"
    assert _source_session_terminal(connection, row)[:2] == (
        "failed",
        "no_authoritative_context",
    )


@pytest.mark.parametrize(
    "row",
    [
        SourceSessionRow(
            "log",
            "claude-wrong",
            datetime(2026, 1, 1, tzinfo=UTC),
            "claude-code",
            (),
            ("thread-id",),
            (),
            (),
        ),
        SourceSessionRow(
            "trace",
            "codex-wrong",
            datetime(2026, 1, 1, tzinfo=UTC),
            "codex-app-server",
            ("session-id",),
            (),
            (),
            ("conversation-id",),
        ),
        SourceSessionRow(
            "log",
            "omp-wrong-signal",
            datetime(2026, 1, 1, tzinfo=UTC),
            "omp",
            (),
            (),
            (),
            ("conversation-id",),
        ),
    ],
)
def test_wrong_native_field_is_rejected_without_projection(
    tmp_path: Path, row: SourceSessionRow
) -> None:
    connection = connect_database(tmp_path / "introspection.sqlite3")
    connection.execute(
        "INSERT INTO scan_runs (id, status, started_at) VALUES ('scan', 'running', ?)",
        ("2026-01-01T00:00:00+00:00",),
    )
    assert row.native_session_ids == ()
    assert row.session_status == "wrong_field"
    assert _source_session_terminal(connection, row)[:2] == (
        "failed",
        "wrong_native_session_field",
    )
    _persist_source_sessions(connection, scan_run_id="scan", rows=[row])
    import json

    (payload_json,) = connection.execute("SELECT payload_json FROM otlp_outbox").fetchone()
    payload = json.loads(payload_json)
    assert payload["source.native_key.status"] == "wrong_field"
    assert not {"source.producer", "source.producer_surface", "source.session.id"} & payload.keys()


def test_observed_exact_unresolved_context_is_failed_not_blocked(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "introspection.sqlite3")
    observed = _row("no-context", ("native-session",))
    assert _source_session_terminal(connection, observed)[:2] == (
        "failed",
        "no_authoritative_context",
    )

    occurred_at = "2026-01-01T00:00:00+00:00"
    for project_id, event_id in (("a" * 64, "c" * 64), ("b" * 64, "d" * 64)):
        connection.execute(
            """INSERT INTO project_identities
            (id, identity_kind, canonical_path, canonical_name, created_at)
            VALUES (?, 'git', ?, ?, ?)""",
            (project_id, f"/{project_id}", project_id, occurred_at),
        )
        connection.execute(
            """INSERT INTO session_context_events (
                event_id, producer, session_id, event_type, occurred_at,
                project_id, project_name, project_root, project_kind
            ) VALUES (?, 'codex-cli', 'ambiguous-session', 'session_context', ?,
                      ?, ?, ?, 'git')""",
            (event_id, occurred_at, project_id, project_id, f"/{project_id}"),
        )

    ambiguous = _row("ambiguous", ("ambiguous-session",))
    assert _source_session_terminal(connection, ambiguous)[:2] == (
        "failed",
        "conflicting_correlation_id",
    )


def test_raw_source_sessions_use_exact_context_and_conserve(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "introspection.sqlite3")
    project_id = "1" * 64
    evidence_id = "2" * 64
    non_git_evidence_id = "3" * 64
    occurred_at = "2025-12-31T00:00:00+00:00"
    connection.execute(
        "INSERT INTO scan_runs (id, status, started_at) VALUES (?, ?, ?)",
        ("scan", "running", occurred_at),
    )
    connection.execute(
        """INSERT INTO project_identities
        (id, identity_kind, canonical_path, canonical_name, created_at)
        VALUES (?, 'git', '/repo', 'repo', ?)""",
        (project_id, occurred_at),
    )
    connection.execute(
        """INSERT INTO session_context_events (
            event_id, producer, session_id, event_type, occurred_at,
            project_id, project_name, project_root, project_kind
        ) VALUES (
            ?, 'codex-cli', 'accepted', 'session_start', ?,
            ?, 'repo', '/repo', 'git'
        )""",
        (evidence_id, occurred_at, project_id),
    )
    connection.execute(
        """INSERT INTO session_context_intervals (
            event_id, producer, session_id, started_at,
            project_id, project_name, project_root, project_kind
        ) VALUES (
            ?, 'codex-cli', 'accepted', ?,
            ?, 'repo', '/repo', 'git'
        )""",
        (evidence_id, occurred_at, project_id),
    )
    connection.execute(
        """INSERT INTO canonical_rejections (
            id, producer, producer_surface, correlation_id, lifecycle_event,
            occurred_at, reason_code, source_adapter, created_at
        ) VALUES (
            ?, 'codex-cli', 'codex-cli', 'non-git', 'session_start',
            ?, 'non_git_workspace', 'test', ?
        )""",
        (non_git_evidence_id, occurred_at, occurred_at),
    )

    outcomes = _persist_source_sessions(
        connection,
        scan_run_id="scan",
        rows=[
            _row("attributed", ("accepted",)),
            _row("missing", ()),
            _row("conflicting", ("one", "two")),
            _row("unresolved", ("unknown",)),
            _row("non-git", ("non-git",)),
        ],
    )

    assert outcomes == {
        "included": 5,
        "attributed": 1,
        "expected_rejection": 1,
        "failed": 3,
        "blocked": 0,
    }
    rows = connection.execute(
        """SELECT source_id, terminal_outcome, terminal_reason, context_evidence_id,
        project_id, project_name, project_root, project_kind
        FROM source_session_records ORDER BY source_id"""
    ).fetchall()
    assert rows == [
        (
            "attributed",
            "attributed",
            "accepted_git_context",
            evidence_id,
            project_id,
            "repo",
            "/repo",
            "git",
        ),
        ("conflicting", "failed", "conflicting_native_session_id", None, None, None, None, None),
        ("missing", "failed", "missing_native_session_id", None, None, None, None, None),
        (
            "non-git",
            "expected_rejection",
            "approved_non_git_workspace",
            non_git_evidence_id,
            None,
            None,
            None,
            None,
        ),
        ("unresolved", "failed", "no_authoritative_context", None, None, None, None, None),
    ]
    statements: list[str] = []
    resolved_intervals = {}
    connection.set_trace_callback(statements.append)
    cached = _source_session_terminal(
        connection,
        _row("cached-one", ("accepted",)),
        resolved_intervals=resolved_intervals,
    )
    assert (
        _source_session_terminal(
            connection,
            _row("cached-two", ("accepted",)),
            resolved_intervals=resolved_intervals,
        )
        == cached
    )
    connection.set_trace_callback(None)
    assert (
        sum("FROM session_context_intervals AS interval" in statement for statement in statements)
        == 1
    )


def test_source_session_events_use_the_gate_six_attribute_contract(tmp_path: Path) -> None:
    import json

    connection = connect_database(tmp_path / "introspection.sqlite3")
    connection.execute(
        "INSERT INTO scan_runs (id, status, started_at) VALUES ('scan', 'running', ?)",
        ("2026-01-01T00:00:00+00:00",),
    )
    rows = [
        _row("exact", ("session-1",)),
        _row("missing", ()),
        _row("conflicting", ("session-1", "session-2")),
        SourceSessionRow(
            source_kind="trace",
            source_id="unmapped-exact",
            source_timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            service_name="unmapped",
            session_ids=("session-3",),
            thread_ids=(),
            legacy_thread_ids=(),
            gen_ai_conversation_ids=(),
        ),
    ]
    _persist_source_sessions(connection, scan_run_id="scan", rows=rows)

    events = {
        payload["source.record.id"]: payload
        for (payload_json,) in connection.execute(
            "SELECT payload_json FROM otlp_outbox ORDER BY event_id"
        ).fetchall()
        for payload in (json.loads(payload_json),)
    }
    required = {
        "source.signal",
        "source.service",
        "source.record.id",
        "source.timestamp_ns",
        "source.native_key.status",
        "source.session_group.id",
        "source.inclusion.status",
        "source.inclusion.reason",
    }
    forbidden_replacements = {
        "source.kind",
        "source.service.name",
        "source.timestamp",
        "source.native_session.id",
    }
    for source_id, native_ids, status in (
        ("exact", ("session-1",), "exact"),
        ("missing", (), "missing"),
        ("conflicting", ("session-1", "session-2"), "conflicting"),
        ("unmapped-exact", (), "wrong_field"),
    ):
        event = events[source_id]
        assert event["event.name"] == "introspection.source_session.recorded"
        assert event["event.scope"] == "source-session"
        assert required <= event.keys()
        assert not (forbidden_replacements & event.keys())
        assert event["source.signal"] == ("trace" if source_id == "unmapped-exact" else "log")
        assert event["source.native_key.status"] == status
        assert event["source.session_group.id"] == _source_session_group_id(native_ids)
        assert event["source.inclusion.status"] == "included"
        assert event["source.inclusion.reason"] == "mapped_to_frozen_source_contract"

    exact = events["exact"]
    assert {
        "source.producer": "codex-cli",
        "source.producer_surface": "codex-cli",
        "source.session.id": "session-1",
    }.items() <= exact.items()
    for source_id in ("missing", "conflicting", "unmapped-exact"):
        assert (
            not {
                "source.producer",
                "source.producer_surface",
                "source.session.id",
            }
            & events[source_id].keys()
        )


def test_raw_current_projection_reconciles_late_codex_context_in_versions(
    tmp_path: Path,
) -> None:
    connection = connect_database(tmp_path / "introspection.sqlite3")
    occurred_at = "2026-01-01T00:00:00+00:00"
    project_id = "4" * 64
    evidence_id = "5" * 64
    codex = _row("shared", ("native-session",))
    claude = SourceSessionRow(
        source_kind="log",
        source_id="shared",
        source_timestamp=codex.source_timestamp,
        service_name="claude-code",
        session_ids=codex.session_ids,
        thread_ids=(),
        legacy_thread_ids=(),
        gen_ai_conversation_ids=(),
    )
    for scan_run_id in ("first", "second"):
        connection.execute(
            "INSERT INTO scan_runs (id, status, started_at) VALUES (?, 'running', ?)",
            (scan_run_id, occurred_at),
        )

    with connection:
        first = _persist_source_sessions(connection, scan_run_id="first", rows=[codex, claude])
        _advance_raw_source_watermark(connection, end_ns=100)
    assert first == {
        "included": 2,
        "attributed": 0,
        "expected_rejection": 0,
        "failed": 2,
        "blocked": 0,
    }
    assert connection.execute(
        "SELECT timestamp_ns, row_id FROM source_watermarks "
        "WHERE source = 'signoz_raw_source_sessions'"
    ).fetchone() == (100, "")

    connection.execute(
        """INSERT INTO project_identities
        (id, identity_kind, canonical_path, canonical_name, created_at)
        VALUES (?, 'git', '/exact/repo', 'repo', ?)""",
        (project_id, occurred_at),
    )
    connection.execute(
        """INSERT INTO session_context_events (
            event_id, producer, session_id, event_type, occurred_at,
            project_id, project_name, project_root, project_kind
        ) VALUES (?, 'codex-cli', 'native-session', 'session_context', ?,
                  ?, 'repo', '/exact/repo', 'git')""",
        (evidence_id, occurred_at, project_id),
    )
    with connection:
        second = _persist_source_sessions(connection, scan_run_id="second", rows=[codex, claude])
        _advance_raw_source_watermark(connection, end_ns=200)
    assert second == {
        "included": 2,
        "attributed": 1,
        "expected_rejection": 0,
        "failed": 1,
        "blocked": 0,
    }
    connection.execute(
        "INSERT INTO scan_runs (id, status, started_at) VALUES (?, 'running', ?)",
        ("replay", occurred_at),
    )
    with connection:
        replay = _persist_source_sessions(connection, scan_run_id="replay", rows=[codex, claude])
    assert replay == second
    assert connection.execute(
        """SELECT service_name, version FROM source_session_current
           WHERE source_id = 'shared' ORDER BY service_name"""
    ).fetchall() == [("claude-code", 1), ("codex_exec", 2)]
    assert connection.execute(
        """SELECT service_name, COUNT(*) FROM source_session_current_versions
           WHERE source_id = 'shared' GROUP BY service_name ORDER BY service_name"""
    ).fetchall() == [("claude-code", 1), ("codex_exec", 2)]
    assert connection.execute(
        """SELECT terminal_outcome, context_evidence_id, project_id, project_name,
                  project_root, project_kind, version
           FROM source_session_current
           WHERE service_name = 'codex_exec' AND source_id = 'shared'"""
    ).fetchone() == (
        "attributed",
        evidence_id,
        project_id,
        "repo",
        "/exact/repo",
        "git",
        2,
    )
    assert connection.execute(
        """SELECT terminal_outcome, version FROM source_session_current
           WHERE service_name = 'claude-code' AND source_id = 'shared'"""
    ).fetchone() == ("failed", 1)
    codex_versions = connection.execute(
        """SELECT version, terminal_outcome, projection_event_id
           FROM source_session_current_versions
           WHERE service_name = 'codex_exec' AND source_id = 'shared'
           ORDER BY version"""
    ).fetchall()
    assert [version[:2] for version in codex_versions] == [(1, "failed"), (2, "attributed")]
    assert codex_versions[0][2] != codex_versions[1][2]
    assert connection.execute(
        """SELECT json_extract(payload_json, '$."entity.version"'),
                  json_extract(payload_json, '$."source.terminal.outcome"')
           FROM otlp_outbox
           WHERE json_extract(payload_json, '$."source.service"') = 'codex_exec'
           ORDER BY json_extract(payload_json, '$."entity.version"')"""
    ).fetchall() == [(1, "failed"), (2, "attributed")]
    assert connection.execute("SELECT COUNT(*) FROM source_session_records").fetchone() == (6,)
    assert connection.execute(
        "SELECT timestamp_ns, row_id FROM source_watermarks "
        "WHERE source = 'signoz_raw_source_sessions'"
    ).fetchone() == (200, "")

    with pytest.raises(RuntimeError), connection:
        _advance_raw_source_watermark(connection, end_ns=300)
        raise RuntimeError("force rollback")
    assert connection.execute(
        "SELECT timestamp_ns FROM source_watermarks WHERE source = 'signoz_raw_source_sessions'"
    ).fetchone() == (200,)


def test_pending_raw_reconciliation_survives_drained_context_and_scan_failure(
    tmp_path: Path,
) -> None:
    connection = connect_database(tmp_path / "introspection.sqlite3")
    occurred_at = datetime(2026, 1, 1, tzinfo=UTC)
    codex = _row("shared", ("native-session",))
    claude = SourceSessionRow(
        source_kind="log",
        source_id="shared",
        source_timestamp=codex.source_timestamp,
        service_name="claude-code",
        session_ids=codex.session_ids,
        thread_ids=(),
        legacy_thread_ids=(),
        gen_ai_conversation_ids=(),
    )
    for scan_run_id in ("first", "reconcile"):
        connection.execute(
            "INSERT INTO scan_runs (id, status, started_at) VALUES (?, 'running', ?)",
            (scan_run_id, occurred_at.isoformat()),
        )
    with connection:
        _persist_source_sessions(connection, scan_run_id="first", rows=[codex, claude])
        _advance_raw_source_watermark(connection, end_ns=100)

    project_id = "6" * 64
    event_id = "7" * 64
    inbox = tmp_path / "inbox"
    spool_event(
        parse_event(
            {
                "event_id": event_id,
                "producer": "codex-cli",
                "session_id": "native-session",
                "event_type": "session_context",
                "occurred_at": occurred_at.isoformat(),
                "agent": {
                    "project": {
                        "id": project_id,
                        "name": "repo",
                        "root": "/exact/repo",
                        "kind": "git",
                    }
                },
            }
        ),
        directory=inbox,
    )
    assert len(drain_inbox(connection, directory=inbox)) == 1
    assert connection.execute(
        """SELECT completed_at FROM source_session_reconciliation_pending
           WHERE producer = 'codex-cli' AND session_id = 'native-session'"""
    ).fetchone() == (None,)

    with pytest.raises(RuntimeError), connection:
        raise RuntimeError("source extraction failed after inbox drain")
    assert connection.execute("SELECT COUNT(*) FROM source_session_records").fetchone() == (2,)
    assert connection.execute(
        "SELECT timestamp_ns FROM source_watermarks WHERE source = 'signoz_raw_source_sessions'"
    ).fetchone() == (100,)

    with connection:
        outcomes = _reconcile_late_source_sessions(connection, scan_run_id="reconcile")
    assert outcomes == {
        "included": 1,
        "attributed": 1,
        "expected_rejection": 0,
        "failed": 0,
        "blocked": 0,
    }
    assert connection.execute(
        """SELECT terminal_outcome, version FROM source_session_current
           WHERE service_name = 'codex_exec' AND source_id = 'shared'"""
    ).fetchone() == ("attributed", 2)
    assert connection.execute(
        """SELECT terminal_outcome, version FROM source_session_current
           WHERE service_name = 'claude-code' AND source_id = 'shared'"""
    ).fetchone() == ("failed", 1)
    assert connection.execute(
        """SELECT completed_at IS NOT NULL FROM source_session_reconciliation_pending
           WHERE producer = 'codex-cli' AND session_id = 'native-session'"""
    ).fetchone() == (1,)
    assert connection.execute(
        """SELECT COUNT(*) FROM otlp_outbox
           WHERE json_extract(payload_json, '$."source.service"') = 'codex_exec'
             AND json_extract(payload_json, '$."entity.version"') = 2"""
    ).fetchone() == (1,)

    conflicting_event_id = "8" * 64
    spool_event(
        parse_event(
            {
                "event_id": conflicting_event_id,
                "producer": "codex-cli",
                "session_id": "native-session",
                "event_type": "session_context",
                "occurred_at": occurred_at.isoformat(),
                "agent": {
                    "project": {
                        "id": "9" * 64,
                        "name": "other-repo",
                        "root": "/other/repo",
                        "kind": "git",
                    }
                },
            }
        ),
        directory=inbox,
    )
    connection.execute(
        "INSERT INTO scan_runs (id, status, started_at) VALUES (?, 'running', ?)",
        ("conflict", occurred_at.isoformat()),
    )
    assert len(drain_inbox(connection, directory=inbox)) == 1
    with connection:
        outcomes = _reconcile_late_source_sessions(connection, scan_run_id="conflict")
    assert outcomes == {
        "included": 1,
        "attributed": 0,
        "expected_rejection": 0,
        "failed": 1,
        "blocked": 0,
    }
    assert connection.execute(
        """SELECT terminal_outcome, terminal_reason, version
           FROM source_session_current
           WHERE service_name = 'codex_exec' AND source_id = 'shared'"""
    ).fetchone() == ("failed", "conflicting_correlation_id", 3)
    assert connection.execute(
        """SELECT version, terminal_outcome, terminal_reason
           FROM source_session_current_versions
           WHERE service_name = 'codex_exec' AND source_id = 'shared'
           ORDER BY version"""
    ).fetchall() == [
        (1, "failed", "no_authoritative_context"),
        (2, "attributed", "accepted_git_context"),
        (3, "failed", "conflicting_correlation_id"),
    ]
    assert connection.execute(
        """SELECT json_extract(payload_json, '$."entity.version"'),
                  json_extract(payload_json, '$."source.terminal.outcome"'),
                  json_extract(payload_json, '$."source.terminal.reason"')
           FROM otlp_outbox
           WHERE json_extract(payload_json, '$."source.service"') = 'codex_exec'
           ORDER BY json_extract(payload_json, '$."entity.version"')"""
    ).fetchall() == [
        (1, "failed", "no_authoritative_context"),
        (2, "attributed", "accepted_git_context"),
        (3, "failed", "conflicting_correlation_id"),
    ]
    assert connection.execute(
        """SELECT completed_at IS NOT NULL FROM source_session_reconciliation_pending
           WHERE producer = 'codex-cli' AND session_id = 'native-session'"""
    ).fetchone() == (1,)
    assert connection.execute(
        """SELECT terminal_outcome, version FROM source_session_current
           WHERE service_name = 'claude-code' AND source_id = 'shared'"""
    ).fetchone() == ("failed", 1)

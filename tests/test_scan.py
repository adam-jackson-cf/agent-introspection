import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import agent_introspection.scan as scan_module
from agent_introspection.capabilities import approve_schema, discover_source_schema
from agent_introspection.config import AppConfig, DatabaseConfig
from agent_introspection.database import connect_database
from agent_introspection.scan import run_scan
from agent_introspection.session_context import (
    SessionContextEvent,
    parse_event,
    spool_event,
)
from agent_introspection.source import (
    ClickHouseClient,
    HydrationIdentityKind,
    HydrationRow,
    LogRow,
    SourceActivityCorrelation,
    SourceCorrelationStatus,
    SourceSessionRow,
    TraceRow,
)


class FakeSource(ClickHouseClient):
    def __init__(
        self,
        logs: list[LogRow] | None = None,
        traces: list[TraceRow] | None = None,
    ) -> None:
        self.log_rows = logs or []
        self.trace_rows = traces or []
        self.log_reads = 0
        self.trace_reads = 0
        self.hydration_batch_sizes: list[int] = []

    def query(self, sql: str, parameters: Mapping[str, str | int]) -> Iterator[dict[str, Any]]:
        del parameters
        if "timezone()" in sql:
            yield {"timezone": "UTC"}
            return
        if "system.columns" in sql:
            names = {
                ("signoz_logs", "distributed_logs_v2"): {
                    "attributes_bool",
                    "attributes_number",
                    "attributes_string",
                    "id",
                    "resource",
                    "span_id",
                    "timestamp",
                    "trace_id",
                    "ts_bucket_start",
                },
                ("signoz_traces", "distributed_signoz_index_v3"): {
                    "attributes_number",
                    "attributes_string",
                    "name",
                    "serviceName",
                    "timestamp",
                    "trace_id",
                    "ts_bucket_start",
                },
            }
            yield from (
                {
                    "database": database,
                    "table": table,
                    "name": name,
                    "type": "String",
                    "default_kind": "",
                    "default_expression": "",
                }
                for (database, table), column_names in names.items()
                for name in sorted(column_names)
            )
            return
        if "system.tables" in sql:
            yield from (
                {"database": "signoz_logs", "name": "distributed_logs_v2"},
                {"database": "signoz_traces", "name": "distributed_signoz_index_v3"},
            )
            return
        yield {"event_names": [], "string_attribute_keys": [], "number_attribute_keys": []}

    def logs(self, *, start_ns: int, end_ns: int) -> Iterator[LogRow]:
        del start_ns, end_ns
        self.log_reads += 1
        yield from self.log_rows

    def traces(self, *, start: datetime, end: datetime) -> Iterator[TraceRow]:
        del start, end
        self.trace_reads += 1
        yield from self.trace_rows

    def raw_source_window_anchor(self) -> tuple[int, int]:
        return 0, 0

    def source_sessions(
        self, *, start: datetime, end: datetime, start_ns: int, end_ns: int
    ) -> Iterator[SourceSessionRow]:
        del start, end, start_ns, end_ns
        for log in self.log_rows:
            yield SourceSessionRow(
                source_kind="log",
                source_id=log.log_id,
                source_timestamp=datetime.fromtimestamp(log.timestamp_ns / 1_000_000_000, tz=UTC),
                service_name=log.service_name or "codex-cli",
                session_ids=tuple(value for value in (log.conversation_id,) if value),
                thread_ids=tuple(value for value in (log.thread_id,) if value),
                legacy_thread_ids=tuple(value for value in (log.legacy_thread_id,) if value),
                gen_ai_conversation_ids=tuple(
                    value for value in (log.gen_ai_conversation_id,) if value
                ),
            )
        for trace in self.trace_rows:
            yield SourceSessionRow(
                source_kind="trace",
                source_id=trace.trace_id,
                source_timestamp=trace.started_at,
                service_name=(trace.service_names[0] if trace.service_names else "codex-cli"),
                session_ids=trace.conversation_ids,
                thread_ids=trace.thread_ids,
                legacy_thread_ids=trace.legacy_thread_ids,
                gen_ai_conversation_ids=trace.gen_ai_conversation_ids,
            )

    def prove_retained_window(self, *, start: datetime, start_ns: int, start_bucket: int) -> None:
        del start, start_ns, start_bucket

    def hydrate(
        self,
        *,
        identity_kind: HydrationIdentityKind,
        identifiers: Sequence[str],
        start_ns: int,
        end_ns: int,
        start_bucket: int,
        end_bucket: int,
    ) -> Iterator[HydrationRow]:
        del identity_kind, start_ns, end_ns, start_bucket, end_bucket
        self.hydration_batch_sizes.append(len(identifiers))
        selected = set(identifiers)
        yield from (
            HydrationRow(
                timestamp_ns=row.timestamp_ns,
                log_id=row.log_id,
                trace_id=row.trace_id,
                span_id=row.span_id,
                event_name=row.event_name,
                call_id=row.call_id,
                tool_name=row.tool_name,
                arguments='{"cmd":"ruff check ."}',
                args=None,
                argv=None,
                assistant_output=None,
                error_message=None,
                outcome=None,
                diagnostic_code=None,
                success_string=row.success_string,
                success_bool=row.success_bool,
                status_code=row.status_code,
                exit_code=1,
            )
            for row in self.log_rows
            if row.log_id in selected
        )


@pytest.fixture
def scan_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, AppConfig]:
    config = AppConfig(database=DatabaseConfig(path=tmp_path / "introspection.sqlite3"))
    connection = connect_database(config.database.path)
    context_inbox = config.database.path.parent / "session-context-inbox"
    monkeypatch.setattr("agent_introspection.scan.inbox_path", lambda _path: context_inbox)
    monkeypatch.setattr("agent_introspection.scan.verify_network_perimeter", lambda **_kwargs: {})
    monkeypatch.setattr(
        "agent_introspection.scan.drain_outbox",
        lambda *_args, **_kwargs: {"selected": 0, "delivered": 0, "pending": 0},
    )
    return connection, config


def approve(connection: Any, source: FakeSource) -> str:
    return approve_schema(
        connection,
        discover_source_schema(source),
        approved_by="test",
    )


def log_row(
    identifier: str,
    timestamp_ns: int,
    *,
    trace_id: str | None = None,
    conversation_id: str | None = None,
    producer: str | None = None,
    thread_id: str | None = None,
    service_name: str | None = None,
) -> LogRow:
    return LogRow(
        timestamp_ns=timestamp_ns,
        log_id=identifier,
        trace_id=trace_id,
        span_id=None,
        event_name="codex.tool_result",
        conversation_id=conversation_id,
        call_id=identifier,
        tool_name="exec_command",
        success_string="false",
        success_bool=None,
        duration_ms=None,
        status_code=None,
        decision=None,
        decision_source=None,
        input_tokens=None,
        output_tokens=None,
        reasoning_tokens=None,
        prompt_length=None,
        producer=producer,
        service_name=service_name,
        thread_id=thread_id,
    )


def _context_event(
    *,
    event_id: str,
    event_type: str,
    root: Path,
    project_id: str,
    occurred_at: datetime,
) -> SessionContextEvent:
    return parse_event(
        {
            "event_id": event_id,
            "producer": "codex-cli",
            "session_id": "session-1",
            "event_type": event_type,
            "occurred_at": occurred_at.isoformat(),
            "agent": {
                "project": {
                    "id": project_id,
                    "name": root.name,
                    "root": root.as_posix(),
                    "kind": "git",
                }
            },
        }
    )


def _trace(identifier: str, occurred_at: datetime) -> TraceRow:
    return TraceRow(
        trace_id=identifier,
        turn_id=None,
        thread_id="session-1",
        started_at=occurred_at - timedelta(seconds=1),
        ended_at=occurred_at,
        total_tokens=0,
        tool_calls=1,
        correlation=SourceActivityCorrelation(
            producer="codex-cli",
            producer_surface="codex-cli",
            correlation_id="session-1",
            source_event_timestamp=occurred_at,
            source_event_ids=(identifier,),
            source_log_ids=(),
            source_span_ids=(f"span-{identifier}",),
        ),
    )


def _activity_rows(connection: Any) -> list[tuple[str, str, int, str | None]]:
    return [
        (str(row[0]), str(row[1]), int(row[2]), None if row[3] is None else str(row[3]))
        for row in connection.execute(
            """
            SELECT a.id, av.attribution_state, av.version, av.project_identity_id
            FROM canonical_activities a
            JOIN canonical_activity_versions av ON av.activity_id = a.id
            WHERE av.version = (
                SELECT MAX(latest.version)
                FROM canonical_activity_versions latest
                WHERE latest.activity_id = a.id
            )
            ORDER BY a.id
            """
        )
    ]


def test_canonical_scan_is_idempotent_and_emits_one_activity_identity(
    scan_environment: tuple[Any, AppConfig], tmp_path: Path
) -> None:
    connection, config = scan_environment
    occurred_at = datetime(2026, 7, 10, 12, tzinfo=UTC)
    root = tmp_path / "workspace"
    root.mkdir()
    spool_event(
        _context_event(
            event_id="a" * 64,
            event_type="session_start",
            root=root,
            project_id="1" * 64,
            occurred_at=occurred_at - timedelta(minutes=1),
        ),
        directory=config.database.path.parent / "session-context-inbox",
    )
    source = FakeSource(
        logs=[
            log_row(
                "log-1",
                int(occurred_at.timestamp() * 1_000_000_000),
                trace_id="trace-1",
                producer="codex-cli",
            ),
            log_row(
                "missing-correlation-log",
                int(occurred_at.timestamp() * 1_000_000_000),
                trace_id="missing-correlation-trace",
                producer="codex-cli",
            ),
            log_row(
                "conflicting-correlation-log",
                int(occurred_at.timestamp() * 1_000_000_000),
                trace_id="conflicting-correlation-trace",
                producer="codex-cli",
            ),
        ],
        traces=[
            _trace("trace-1", occurred_at),
            replace(
                _trace("missing-correlation-trace", occurred_at),
                correlation=None,
                correlation_status=SourceCorrelationStatus(
                    state="missing",
                    producer="codex-cli",
                    producer_surface="codex-cli",
                    source_event_timestamp=occurred_at,
                    source_span_ids=("missing-span",),
                ),
            ),
            replace(
                _trace("conflicting-correlation-trace", occurred_at),
                correlation=None,
                correlation_status=SourceCorrelationStatus(
                    state="conflicting",
                    producer="codex-cli",
                    producer_surface="codex-cli",
                    source_event_timestamp=occurred_at,
                    source_span_ids=("conflicting-span",),
                ),
            ),
        ],
    )
    approve(connection, source)

    first = run_scan(connection, config, client=source, end_time=occurred_at)
    first_rows = _activity_rows(connection)
    run_scan(connection, config, client=source, end_time=occurred_at + timedelta(seconds=1))

    assert first["status"] == "succeeded"
    assert first["observations"] == 1
    assert len(first_rows) == 1
    activity_id, state, version, project_id = first_rows[0]
    assert state == "resolved"
    assert version == 1
    assert project_id == "1" * 64
    assert connection.execute(
        "SELECT id, canonical_path, canonical_name FROM project_identities"
    ).fetchone() == ("1" * 64, root.as_posix(), root.name)
    assert _activity_rows(connection) == first_rows
    assert connection.execute(
        "SELECT COUNT(*) FROM canonical_activity_versions WHERE activity_id = ?", (activity_id,)
    ).fetchone() == (1,)
    rejection_rows = connection.execute(
        """
        SELECT reason_code, producer, producer_surface, correlation_id, occurred_at,
               source_adapter, source_provenance
        FROM canonical_rejections
        ORDER BY reason_code
        """
    ).fetchall()
    assert rejection_rows == [
        (
            "conflicting_correlation_id",
            "codex-cli",
            "codex-cli",
            None,
            occurred_at.isoformat(),
            "signoz",
            '{"source_event_ids":[],"source_log_ids":[],"source_span_ids":["conflicting-span"]}',
        ),
        (
            "missing_correlation_id",
            "codex-cli",
            "codex-cli",
            None,
            occurred_at.isoformat(),
            "signoz",
            '{"source_event_ids":[],"source_log_ids":[],"source_span_ids":["missing-span"]}',
        ),
    ]
    outbox = [
        json.loads(row[0])
        for row in connection.execute("SELECT payload_json FROM otlp_outbox")
        if json.loads(row[0])["event.name"] == "introspection.activity.version.recorded"
    ]
    assert [(event["activity.id"], event["activity.version"]) for event in outbox] == [
        (activity_id, 1)
    ]
    activity_event = outbox[0]
    assert activity_event["activity.producer"] == "codex-cli"
    assert activity_event["activity.producer_surface"] == "codex-cli"
    assert activity_event["activity.correlation_id"] == "session-1"
    assert activity_event["activity.detector.id"] == "tool_failure"
    assert activity_event["activity.payload_schema_version"] == 2
    assert activity_event["agent.project.id"] == "1" * 64
    assert activity_event["agent.project.name"] == root.name


def test_late_context_bumps_one_canonical_activity_once(
    scan_environment: tuple[Any, AppConfig], tmp_path: Path
) -> None:
    connection, config = scan_environment
    occurred_at = datetime(2026, 7, 10, 12, tzinfo=UTC)
    root = tmp_path / "workspace"
    root.mkdir()
    source = FakeSource(
        logs=[
            log_row(
                "log-1",
                int(occurred_at.timestamp() * 1_000_000_000),
                trace_id="trace-1",
                producer="codex-cli",
            )
        ],
        traces=[_trace("trace-1", occurred_at)],
    )
    approve(connection, source)

    run_scan(connection, config, client=source, end_time=occurred_at)
    activity_id, state, version, project_id = _activity_rows(connection)[0]
    assert (state, version, project_id) == ("unresolved", 1, None)

    context = _context_event(
        event_id="b" * 64,
        event_type="session_start",
        root=root,
        project_id="2" * 64,
        occurred_at=occurred_at - timedelta(minutes=1),
    )
    spool_event(
        context,
        directory=config.database.path.parent / "session-context-inbox",
    )
    run_scan(connection, config, client=source, end_time=occurred_at + timedelta(seconds=1))
    spool_event(
        context,
        directory=config.database.path.parent / "session-context-inbox",
    )
    run_scan(connection, config, client=source, end_time=occurred_at + timedelta(seconds=2))

    assert _activity_rows(connection) == [(activity_id, "resolved", 2, "2" * 64)]
    schedule = connection.execute(
        """
        SELECT aggregate_kind, completed_at
        FROM canonical_recomputation_schedule
        WHERE activity_id = ? AND activity_version = 2
        ORDER BY aggregate_kind
        """,
        (activity_id,),
    ).fetchall()
    assert [row[0] for row in schedule] == ["findings", "trends"]
    assert all(row[1] is not None for row in schedule)
    assert connection.execute(
        "SELECT COUNT(*) FROM canonical_finding_membership WHERE activity_id = ?",
        (activity_id,),
    ).fetchone() == (2,)
    assert connection.execute(
        """
        SELECT COUNT(*)
        FROM canonical_finding_membership AS membership
        JOIN findings ON findings.id = membership.finding_id
        WHERE membership.activity_id = ? AND findings.is_active = 1
        """,
        (activity_id,),
    ).fetchone() == (1,)


def test_codex_session_context_resolves_and_conflicts_fail_closed(
    scan_environment: tuple[Any, AppConfig], tmp_path: Path
) -> None:
    connection, config = scan_environment
    occurred_at = datetime(2026, 7, 10, 12, tzinfo=UTC)
    root = tmp_path / "workspace"
    root.mkdir()
    source = FakeSource(
        logs=[
            log_row(
                "log-1",
                int(occurred_at.timestamp() * 1_000_000_000),
                trace_id="trace-1",
                producer="codex-cli",
            )
        ],
        traces=[_trace("trace-1", occurred_at)],
    )
    approve(connection, source)

    run_scan(connection, config, client=source, end_time=occurred_at)
    activity_id, state, version, project_id = _activity_rows(connection)[0]
    assert (state, version, project_id) == ("unresolved", 1, None)

    context = _context_event(
        event_id="e" * 64,
        event_type="session_context",
        root=root,
        project_id="5" * 64,
        occurred_at=occurred_at + timedelta(seconds=1),
    )
    inbox = config.database.path.parent / "session-context-inbox"
    spool_event(context, directory=inbox)
    run_scan(connection, config, client=source, end_time=occurred_at + timedelta(seconds=1))

    assert _activity_rows(connection) == [(activity_id, "resolved", 2, "5" * 64)]
    assert connection.execute(
        "SELECT COUNT(*) FROM canonical_activity_versions WHERE activity_id = ?",
        (activity_id,),
    ).fetchone() == (2,)
    assert connection.execute(
        """
        SELECT attribution_method, attribution_evidence_id
        FROM canonical_activity_versions
        WHERE activity_id = ? AND version = 2
        """,
        (activity_id,),
    ).fetchone() == ("session_context", context.event_id)
    assert connection.execute(
        "SELECT id FROM project_identities WHERE id = ?", ("5" * 64,)
    ).fetchone() == ("5" * 64,)

    spool_event(context, directory=inbox)
    run_scan(connection, config, client=source, end_time=occurred_at + timedelta(seconds=2))
    assert _activity_rows(connection) == [(activity_id, "resolved", 2, "5" * 64)]

    conflicting_root = tmp_path / "conflicting-workspace"
    conflicting_root.mkdir()
    spool_event(
        _context_event(
            event_id="f" * 64,
            event_type="session_context",
            root=conflicting_root,
            project_id="6" * 64,
            occurred_at=occurred_at + timedelta(seconds=3),
        ),
        directory=inbox,
    )
    run_scan(connection, config, client=source, end_time=occurred_at + timedelta(seconds=3))

    assert _activity_rows(connection) == [(activity_id, "unresolved", 3, None)]


def test_workspace_transition_splits_canonical_activities(
    scan_environment: tuple[Any, AppConfig], tmp_path: Path
) -> None:
    connection, config = scan_environment
    before = datetime(2026, 7, 10, 12, tzinfo=UTC)
    after = before + timedelta(minutes=1)
    first_root = tmp_path / "one"
    second_root = tmp_path / "two"
    first_root.mkdir()
    second_root.mkdir()
    inbox = config.database.path.parent / "session-context-inbox"
    spool_event(
        _context_event(
            event_id="c" * 64,
            event_type="session_start",
            root=first_root,
            project_id="3" * 64,
            occurred_at=before - timedelta(minutes=1),
        ),
        directory=inbox,
    )
    spool_event(
        _context_event(
            event_id="d" * 64,
            event_type="workspace_changed",
            root=second_root,
            project_id="4" * 64,
            occurred_at=after - timedelta(seconds=1),
        ),
        directory=inbox,
    )
    source = FakeSource(
        logs=[
            log_row("log-1", int(before.timestamp() * 1_000_000_000), trace_id="trace-1"),
            log_row("log-2", int(after.timestamp() * 1_000_000_000), trace_id="trace-1"),
        ],
        traces=[_trace("trace-1", after)],
    )
    approve(connection, source)

    result = run_scan(connection, config, client=source, end_time=after)

    assert result["observations"] == 4
    rows = _activity_rows(connection)
    assert len(rows) == 4
    assert {row[1:3] for row in rows} == {("resolved", 1)}
    assert {row[3] for row in rows} == {"3" * 64, "4" * 64}
    transition_ns = int((after - timedelta(seconds=1)).timestamp() * 1_000_000_000)
    memberships = connection.execute(
        """
        SELECT source_started_at_ns, source_ended_at_ns, source_membership_json
        FROM canonical_activities
        """
    ).fetchall()
    assert all(
        ended_at_ns < transition_ns or started_at_ns >= transition_ns
        for started_at_ns, ended_at_ns, _ in memberships
    )
    assert all(len(json.loads(membership)["log_ids"]) == 1 for _, _, membership in memberships)
    projects = connection.execute(
        """
        SELECT id, canonical_path, canonical_name
        FROM project_identities
        ORDER BY id
        """
    ).fetchall()
    assert projects == [
        ("3" * 64, first_root.as_posix(), first_root.name),
        ("4" * 64, second_root.as_posix(), second_root.name),
    ]

    run_scan(connection, config, client=source, end_time=after + timedelta(seconds=1))

    assert _activity_rows(connection) == rows
    assert (
        connection.execute(
            "SELECT id, canonical_path, canonical_name FROM project_identities ORDER BY id"
        ).fetchall()
        == projects
    )


def test_run_scan_closes_context_obligation_after_persisting_first_raw(
    scan_environment: tuple[Any, AppConfig], tmp_path: Path
) -> None:
    connection, config = scan_environment
    occurred_at = datetime(2026, 7, 10, 12, tzinfo=UTC)
    root = tmp_path / "workspace"
    root.mkdir()
    source = FakeSource(
        logs=[
            log_row(
                "raw-1",
                int(occurred_at.timestamp() * 1_000_000_000),
                thread_id="session-1",
                producer="codex-cli",
            )
        ]
    )
    approve(connection, source)
    spool_event(
        _context_event(
            event_id="a" * 64,
            event_type="session_context",
            root=root,
            project_id="1" * 64,
            occurred_at=occurred_at,
        ),
        directory=config.database.path.parent / "session-context-inbox",
    )

    run_scan(connection, config, client=source, end_time=occurred_at)

    assert connection.execute(
        """SELECT terminal_outcome FROM source_session_current
           WHERE source_kind = 'log' AND service_name = 'codex-cli' AND source_id = 'raw-1'"""
    ).fetchone() == ("attributed",)
    assert connection.execute(
        """SELECT completed_at IS NOT NULL FROM source_session_reconciliation_pending
           WHERE producer = 'codex-cli' AND session_id = 'session-1'"""
    ).fetchone() == (1,)


def test_run_scan_keeps_accepted_context_pending_without_raw_population(
    scan_environment: tuple[Any, AppConfig], tmp_path: Path
) -> None:
    connection, config = scan_environment
    occurred_at = datetime(2026, 7, 10, 12, tzinfo=UTC)
    root = tmp_path / "workspace"
    root.mkdir()
    source = FakeSource()
    approve(connection, source)
    spool_event(
        _context_event(
            event_id="c" * 64,
            event_type="session_context",
            root=root,
            project_id="1" * 64,
            occurred_at=occurred_at,
        ),
        directory=config.database.path.parent / "session-context-inbox",
    )

    run_scan(connection, config, client=source, end_time=occurred_at)

    assert connection.execute(
        """SELECT completed_at IS NULL FROM source_session_reconciliation_pending
           WHERE producer = 'codex-cli' AND session_id = 'session-1'"""
    ).fetchone() == (1,)


def test_run_scan_recloses_later_context_for_attributed_raw(
    scan_environment: tuple[Any, AppConfig], tmp_path: Path
) -> None:
    connection, config = scan_environment
    occurred_at = datetime(2026, 7, 10, 12, tzinfo=UTC)
    root = tmp_path / "workspace"
    root.mkdir()
    source = FakeSource(
        logs=[
            log_row(
                "raw-1",
                int(occurred_at.timestamp() * 1_000_000_000),
                thread_id="session-1",
                producer="codex-cli",
            )
        ]
    )
    approve(connection, source)
    inbox = config.database.path.parent / "session-context-inbox"
    for event_id, at in (("a" * 64, occurred_at), ("b" * 64, occurred_at + timedelta(seconds=1))):
        spool_event(
            _context_event(
                event_id=event_id,
                event_type="session_context",
                root=root,
                project_id="1" * 64,
                occurred_at=at,
            ),
            directory=inbox,
        )
        run_scan(connection, config, client=source, end_time=at)

    assert connection.execute(
        """SELECT completed_at IS NOT NULL FROM source_session_reconciliation_pending
           WHERE producer = 'codex-cli' AND session_id = 'session-1'"""
    ).fetchone() == (1,)


def test_run_scan_rolls_back_reconciliation_and_recovers_durable_raw(
    scan_environment: tuple[Any, AppConfig], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection, config = scan_environment
    occurred_at = datetime(2026, 7, 10, 12, tzinfo=UTC)
    root = tmp_path / "workspace"
    root.mkdir()
    source = FakeSource(
        logs=[
            log_row(
                "raw-1",
                int(occurred_at.timestamp() * 1_000_000_000),
                thread_id="session-1",
                producer="codex-cli",
            ),
            log_row(
                "raw-2",
                int(occurred_at.timestamp() * 1_000_000_000),
                thread_id="session-1",
                producer="codex-cli",
            ),
            log_row(
                "claude-raw",
                int(occurred_at.timestamp() * 1_000_000_000),
                conversation_id="session-1",
                service_name="claude-code",
                producer="claude-code",
            ),
        ]
    )
    approve(connection, source)
    run_scan(connection, config, client=source, end_time=occurred_at)
    spool_event(
        _context_event(
            event_id="a" * 64,
            event_type="session_context",
            root=root,
            project_id="1" * 64,
            occurred_at=occurred_at + timedelta(seconds=1),
        ),
        directory=config.database.path.parent / "session-context-inbox",
    )
    original = scan_module._reconcile_late_source_sessions

    def fail_after_reconciliation(*args: Any, **kwargs: Any) -> dict[str, int]:
        original(*args, **kwargs)
        raise RuntimeError("forced reconciliation rollback")

    monkeypatch.setattr(scan_module, "_reconcile_late_source_sessions", fail_after_reconciliation)
    with pytest.raises(RuntimeError, match="forced reconciliation rollback"):
        run_scan(connection, config, client=source, end_time=occurred_at + timedelta(seconds=1))
    assert connection.execute(
        """
        SELECT source_id, terminal_outcome, version
        FROM source_session_current
        WHERE source_kind = 'log' AND service_name = 'codex-cli'
        ORDER BY source_id
        """
    ).fetchall() == [("raw-1", "failed", 1), ("raw-2", "failed", 1)]
    assert connection.execute(
        """
        SELECT terminal_outcome, version FROM source_session_current
        WHERE source_kind = 'log' AND service_name = 'claude-code'
          AND source_id = 'claude-raw'
        """
    ).fetchone() == ("failed", 1)
    assert connection.execute(
        """SELECT completed_at FROM source_session_reconciliation_pending
           WHERE producer = 'codex-cli' AND session_id = 'session-1'"""
    ).fetchone() == (None,)
    assert connection.execute(
        """
        SELECT COUNT(*) FROM otlp_outbox
        WHERE json_extract(payload_json, '$."source.service"') = 'codex-cli'
          AND json_extract(payload_json, '$."entity.version"') = 2
        """
    ).fetchone() == (0,)

    monkeypatch.setattr(scan_module, "_reconcile_late_source_sessions", original)
    source.log_rows.clear()
    run_scan(connection, config, client=source, end_time=occurred_at + timedelta(seconds=2))

    assert connection.execute(
        """
        SELECT source_id, terminal_outcome, version, project_id
        FROM source_session_current
        WHERE source_kind = 'log' AND service_name = 'codex-cli'
        ORDER BY source_id
        """
    ).fetchall() == [
        ("raw-1", "attributed", 2, "1" * 64),
        ("raw-2", "attributed", 2, "1" * 64),
    ]
    assert connection.execute(
        """
        SELECT json_extract(payload_json, '$."source.record.id"'),
               json_extract(payload_json, '$."source.terminal.outcome"'),
               json_extract(payload_json, '$."entity.version"')
        FROM otlp_outbox
        WHERE json_extract(payload_json, '$."source.service"') = 'codex-cli'
          AND json_extract(payload_json, '$."entity.version"') = 2
        ORDER BY json_extract(payload_json, '$."source.record.id"')
        """
    ).fetchall() == [("raw-1", "attributed", 2), ("raw-2", "attributed", 2)]
    assert connection.execute(
        """
        SELECT terminal_outcome, version FROM source_session_current
        WHERE source_kind = 'log' AND service_name = 'claude-code'
          AND source_id = 'claude-raw'
        """
    ).fetchone() == ("failed", 1)
    assert connection.execute(
        """SELECT completed_at IS NOT NULL FROM source_session_reconciliation_pending
           WHERE producer = 'codex-cli' AND session_id = 'session-1'"""
    ).fetchone() == (1,)


def test_raw_source_claim_precedes_query_retries_exact_window_and_advances(
    scan_environment: tuple[Any, AppConfig],
) -> None:
    connection, config = scan_environment
    first_end = datetime(2026, 1, 1, tzinfo=UTC)

    class ClaimObservingSource(FakeSource):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[tuple[int, int]] = []
            self.fail = True

        def raw_source_window_anchor(self) -> tuple[int, int]:
            return 10, 20

        def source_sessions(
            self, *, start: datetime, end: datetime, start_ns: int, end_ns: int
        ) -> Iterator[SourceSessionRow]:
            assert connection.execute(
                """
                SELECT start_ns, end_ns FROM raw_source_window_claims
                WHERE source = 'signoz_raw_source_sessions'
                  AND start_ns = ? AND end_ns = ?
                """,
                (start_ns, end_ns),
            ).fetchone() == (start_ns, end_ns)
            self.calls.append((start_ns, end_ns))
            if self.fail:
                raise RuntimeError("raw source query failed")
            return iter(())

    source = ClaimObservingSource()
    approve(connection, source)
    with pytest.raises(RuntimeError, match="raw source query failed"):
        run_scan(connection, config, client=source, end_time=first_end)

    first_window = (20, 20 + scan_module._ACTIVITY_FORWARD_WINDOW_SECONDS * 1_000_000_000)
    assert source.calls == [first_window]
    assert (
        connection.execute(
            "SELECT timestamp_ns FROM source_watermarks WHERE source = 'signoz_raw_source_sessions'"
        ).fetchone()
        is None
    )
    assert (
        connection.execute(
            "SELECT timestamp_ns FROM source_watermarks WHERE source = 'signoz_logs'"
        ).fetchone()
        is None
    )
    assert connection.execute(
        "SELECT start_ns, end_ns FROM raw_source_window_claims"
    ).fetchall() == [first_window]

    source.fail = False
    second_end = first_end + timedelta(seconds=10)
    run_scan(connection, config, client=source, end_time=second_end)
    assert source.calls == [first_window, first_window]
    assert connection.execute(
        "SELECT timestamp_ns FROM source_watermarks WHERE source = 'signoz_raw_source_sessions'"
    ).fetchone() == (first_window[1],)
    assert connection.execute(
        "SELECT timestamp_ns FROM source_watermarks WHERE source = 'signoz_logs'"
    ).fetchone() == (first_window[1],)

    third_end = second_end + timedelta(seconds=10)
    run_scan(connection, config, client=source, end_time=third_end)
    assert source.calls[-1] == (
        first_window[1],
        first_window[1] + scan_module._ACTIVITY_FORWARD_WINDOW_SECONDS * 1_000_000_000,
    )


def test_raw_source_cursor_bounds_stale_activity_replays(
    scan_environment: tuple[Any, AppConfig],
) -> None:
    connection, config = scan_environment
    first_end = datetime(2026, 1, 1, tzinfo=UTC)

    class WindowRecordingSource(FakeSource):
        def __init__(self) -> None:
            super().__init__()
            self.log_windows: list[tuple[int, int]] = []
            self.trace_windows: list[tuple[datetime, datetime]] = []
            self.anchor_reads = 0

        def raw_source_window_anchor(self) -> tuple[int, int]:
            self.anchor_reads += 1
            if self.anchor_reads > 1:
                raise RuntimeError("approved raw source anchor was queried again")
            return 0, 0

        def logs(self, *, start_ns: int, end_ns: int) -> Iterator[LogRow]:
            self.log_windows.append((start_ns, end_ns))
            yield from super().logs(start_ns=start_ns, end_ns=end_ns)

        def traces(self, *, start: datetime, end: datetime) -> Iterator[TraceRow]:
            self.trace_windows.append((start, end))
            yield from super().traces(start=start, end=end)

    source = WindowRecordingSource()
    approve(connection, source)
    run_scan(connection, config, client=source, end_time=first_end)

    first_end_ns = int(first_end.timestamp() * 1_000_000_000)
    assert connection.execute(
        "SELECT timestamp_ns FROM source_watermarks WHERE source = 'signoz_logs'"
    ).fetchone() == (first_end_ns,)
    with connection:
        connection.execute(
            "UPDATE source_watermarks SET timestamp_ns = 0 WHERE source = 'signoz_logs'"
        )

    second_end = first_end + timedelta(hours=3)
    run_scan(connection, config, client=source, end_time=second_end)

    bounded_end = first_end + timedelta(seconds=scan_module._ACTIVITY_FORWARD_WINDOW_SECONDS)
    assert bounded_end - first_end <= timedelta(minutes=15)
    bounded_end_ns = int(bounded_end.timestamp() * 1_000_000_000)
    expected_start_ns = first_end_ns - config.lifecycle.clock_skew_seconds * 1_000_000_000
    assert source.log_windows == [(0, first_end_ns), (expected_start_ns, bounded_end_ns)]
    assert source.trace_windows == [
        (datetime.fromtimestamp(0, tz=UTC), first_end),
        (datetime.fromtimestamp(expected_start_ns / 1_000_000_000, tz=UTC), bounded_end),
    ]
    assert connection.execute(
        "SELECT timestamp_ns FROM source_watermarks WHERE source = 'signoz_logs'"
    ).fetchone() == (bounded_end_ns,)
    assert source.anchor_reads == 1

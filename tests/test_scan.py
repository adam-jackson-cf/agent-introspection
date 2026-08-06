import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agent_introspection.capabilities import approve_schema, discover_source_schema
from agent_introspection.config import AppConfig, DatabaseConfig
from agent_introspection.database import connect_database
from agent_introspection.scan import run_scan
from agent_introspection.session_context import parse_event, spool_event
from agent_introspection.source import (
    ClickHouseClient,
    HydrationRow,
    LogRow,
    SourceActivityCorrelation,
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

    def query(self, sql: str, _parameters: object) -> list[dict[str, Any]]:
        if "timezone()" in sql:
            return [{"timezone": "UTC"}]
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
            return [
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
            ]
        if "system.tables" in sql:
            return [
                {"database": "signoz_logs", "name": "distributed_logs_v2"},
                {"database": "signoz_traces", "name": "distributed_signoz_index_v3"},
            ]
        return [{"event_names": [], "string_attribute_keys": [], "number_attribute_keys": []}]

    def logs(self, **_bounds: object) -> list[LogRow]:
        self.log_reads += 1
        return self.log_rows

    def traces(self, **_bounds: object) -> list[TraceRow]:
        self.trace_reads += 1
        return self.trace_rows

    def prove_retained_window(self, **_bounds: object) -> None:
        return None

    def hydrate(self, *, identifiers: list[str], **_bounds: object) -> list[HydrationRow]:
        self.hydration_batch_sizes.append(len(identifiers))
        selected = set(identifiers)
        return [
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
        ]


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
    )


def _context_event(
    *,
    event_id: str,
    event_type: str,
    root: Path,
    project_id: str,
    occurred_at: datetime,
) -> object:
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
                "uncorrelated-log",
                int(occurred_at.timestamp() * 1_000_000_000),
                trace_id="uncorrelated-trace",
                producer="codex-cli",
            ),
        ],
        traces=[
            _trace("trace-1", occurred_at),
            replace(_trace("uncorrelated-trace", occurred_at), correlation=None),
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
    outbox = [
        json.loads(row[0])
        for row in connection.execute("SELECT payload_json FROM otlp_outbox")
        if json.loads(row[0])["event.name"] == "introspection.activity.version.recorded"
    ]
    assert [(event["activity.id"], event["activity.version"]) for event in outbox] == [
        (activity_id, 1)
    ]


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
            log_row("log-2", int(after.timestamp() * 1_000_000_000), trace_id="trace-2"),
        ],
        traces=[_trace("trace-1", before), _trace("trace-2", after)],
    )
    approve(connection, source)

    result = run_scan(connection, config, client=source, end_time=after)

    assert result["observations"] == 3
    rows = _activity_rows(connection)
    assert len(rows) == 3
    assert {row[1:3] for row in rows} == {("resolved", 1)}
    assert {row[3] for row in rows} == {"3" * 64, "4" * 64}
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

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agent_introspection.capabilities import (
    CapabilityError,
    approve_schema,
    discover_source_schema,
)
from agent_introspection.config import AppConfig, DatabaseConfig, SchedulerConfig
from agent_introspection.database import connect_database
from agent_introspection.scan import run_scan
from agent_introspection.source import ClickHouseClient, HydrationRow, LogRow, TraceRow


class FakeSource(ClickHouseClient):
    def __init__(
        self, logs: list[LogRow] | None = None, traces: list[TraceRow] | None = None
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
    monkeypatch.setattr("agent_introspection.scan.verify_network_perimeter", lambda **_kwargs: {})
    monkeypatch.setattr(
        "agent_introspection.scan.drain_outbox",
        lambda *_args, **_kwargs: {"selected": 0, "delivered": 0, "pending": 0},
    )
    return connection, config


def approve(connection: Any, source: FakeSource) -> None:
    approve_schema(
        connection,
        discover_source_schema(source),
        approved_by="test",
    )


def log_row(identifier: str, timestamp_ns: int, *, trace_id: str | None = None) -> LogRow:
    return LogRow(
        timestamp_ns=timestamp_ns,
        log_id=identifier,
        trace_id=trace_id,
        span_id=None,
        event_name="codex.tool_result",
        conversation_id=None,
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
    )


def test_valid_no_data_and_each_single_source_scan(scan_environment: tuple[Any, AppConfig]) -> None:
    connection, config = scan_environment
    now = datetime(2026, 7, 10, 12, tzinfo=UTC)
    empty = FakeSource()
    approve(connection, empty)
    assert run_scan(connection, config, client=empty, end_time=now)["status"] == "no_data"

    trace_only = FakeSource(
        traces=[
            TraceRow("trace-1", "turn-1", "thread-1", None, now - timedelta(seconds=1), now, 10, 0)
        ]
    )
    assert (
        run_scan(connection, config, client=trace_only, end_time=now + timedelta(seconds=1))[
            "traces"
        ]
        == 1
    )

    log_only = FakeSource(logs=[log_row("log-1", int(now.timestamp() * 1_000_000_000))])
    result = run_scan(connection, config, client=log_only, end_time=now + timedelta(seconds=2))
    assert result["logs"] == 1
    assert result["observations"] == 1


def test_unapproved_source_schema_stops_before_extraction(
    scan_environment: tuple[Any, AppConfig],
) -> None:
    connection, config = scan_environment
    source = FakeSource(logs=[log_row("unread", 1)])
    with pytest.raises(CapabilityError, match="schema drift"):
        run_scan(
            connection,
            config,
            client=source,
            end_time=datetime(2026, 7, 10, 12, tzinfo=UTC),
        )
    assert source.log_reads == 0
    assert source.trace_reads == 0


def test_configured_lease_duration_controls_scan_overlap_exclusion(
    scan_environment: tuple[Any, AppConfig], monkeypatch: pytest.MonkeyPatch
) -> None:
    connection, base_config = scan_environment
    config = AppConfig(
        database=base_config.database,
        signoz=base_config.signoz,
        scheduler=SchedulerConfig(lease_seconds=123),
    )
    source = FakeSource()
    approve(connection, source)
    durations: list[timedelta] = []

    def reject_overlap(_connection: Any, *, duration: timedelta) -> None:
        durations.append(duration)
        raise RuntimeError("scheduler lease 'scan' is already held")

    monkeypatch.setattr("agent_introspection.scheduler.acquire_lease", reject_overlap)
    with pytest.raises(RuntimeError, match="already held"):
        run_scan(
            connection,
            config,
            client=source,
            end_time=datetime(2026, 7, 10, 12, tzinfo=UTC),
        )
    assert durations == [timedelta(seconds=123)]
    assert source.log_reads == 0
    assert source.trace_reads == 0


def test_overlap_is_idempotent_and_hydration_is_batched(
    scan_environment: tuple[Any, AppConfig],
) -> None:
    connection, config = scan_environment
    now = datetime(2026, 7, 10, 12, tzinfo=UTC)
    rows = [
        log_row(f"log-{index}", int(now.timestamp() * 1_000_000_000) + index)
        for index in range(501)
    ]
    source = FakeSource(logs=rows)
    approve(connection, source)
    first = run_scan(connection, config, client=source, end_time=now + timedelta(seconds=1))
    second = run_scan(connection, config, client=source, end_time=now + timedelta(seconds=2))
    assert first["observations"] > 0
    assert second["observations"] == 0
    assert source.hydration_batch_sizes[:3] == [250, 250, 1]
    persisted = connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    assert persisted == first["observations"]


def test_actionable_findings_become_dormant_with_zero_current_window_counts(
    scan_environment: tuple[Any, AppConfig],
) -> None:
    connection, config = scan_environment
    now = datetime(2026, 7, 10, 12, tzinfo=UTC)
    project = config.database.path.parent / "project"
    project.mkdir()
    occurred_at = [now - timedelta(days=2), now - timedelta(days=1), now]
    rows = [
        log_row(
            f"log-{index}",
            int(timestamp.timestamp() * 1_000_000_000),
            trace_id=f"trace-{index}",
        )
        for index, timestamp in enumerate(occurred_at)
    ]
    traces = [
        TraceRow(
            f"trace-{index}",
            f"turn-{index}",
            f"thread-{index}",
            project.as_posix(),
            timestamp - timedelta(seconds=1),
            timestamp,
            0,
            0,
        )
        for index, timestamp in enumerate(occurred_at)
    ]
    source = FakeSource(logs=rows, traces=traces)
    approve(connection, source)

    first = run_scan(connection, config, client=source, end_time=now)
    assert first["status"] == "succeeded"
    finding_id = connection.execute(
        "SELECT id FROM findings WHERE trend_state = 'actionable'"
    ).fetchone()
    assert finding_id is not None

    source.log_rows = []
    source.trace_rows = []
    second = run_scan(connection, config, client=source, end_time=now + timedelta(days=8))
    assert second["status"] == "no_data"
    assert connection.execute(
        """
        SELECT trend_state, occurrence_count, canonical_task_count, local_day_count
        FROM findings WHERE id = ?
        """,
        finding_id,
    ).fetchone() == ("dormant", 0, 0, 0)
    assert connection.execute(
        """
        SELECT trend_state, occurrence_count, canonical_task_count, local_day_count
        FROM trend_evaluations
        WHERE finding_id = ? AND trend_state = 'dormant'
        """,
        finding_id,
    ).fetchone() == ("dormant", 0, 0, 0)


def test_failed_scan_rolls_back_its_extraction_window(
    scan_environment: tuple[Any, AppConfig], monkeypatch: pytest.MonkeyPatch
) -> None:
    connection, config = scan_environment
    now = datetime(2026, 7, 10, 12, tzinfo=UTC)
    source = FakeSource(logs=[log_row("log-1", int(now.timestamp() * 1_000_000_000))])
    approve(connection, source)

    def fail_after_persistence(*_args: object, **_kwargs: object) -> list[object]:
        raise RuntimeError("trend persistence failed")

    monkeypatch.setattr("agent_introspection.scan._update_findings", fail_after_persistence)
    with pytest.raises(RuntimeError, match="trend persistence failed"):
        run_scan(connection, config, client=source, end_time=now)

    assert connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM finding_membership").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM source_watermarks").fetchone()[0] == 0
    assert connection.execute("SELECT status, error_code FROM scan_runs").fetchone() == (
        "failed",
        "RuntimeError",
    )

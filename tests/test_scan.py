import json
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agent_introspection import generations, scan
from agent_introspection.capabilities import (
    CapabilityError,
    approve_schema,
    discover_source_schema,
)
from agent_introspection.config import AppConfig, DatabaseConfig, SchedulerConfig
from agent_introspection.database import connect_database
from agent_introspection.generations import GenerationError
from agent_introspection.identities import ProjectIdentity
from agent_introspection.project_evidence import (
    ConversationProjectInterval,
    DirectProjectEvidence,
    ProjectEvidence,
)
from agent_introspection.scan import (
    PipelineStream,
    _detector_events,
    _pipeline_snapshot_event,
    reanalyse_attribution,
    run_scan,
)
from agent_introspection.source import (
    ClickHouseClient,
    HydrationRow,
    LogRow,
    ProjectEvidenceRow,
    SourceError,
    TraceRow,
)
from agent_introspection.telemetry import OPERATIONAL_SCOPE, DerivedEvent, enqueue_events


class FakeSource(ClickHouseClient):
    def __init__(
        self,
        logs: list[LogRow] | None = None,
        traces: list[TraceRow] | None = None,
        project_evidence: list[ProjectEvidenceRow] | None = None,
    ) -> None:
        self.log_rows = logs or []
        self.trace_rows = traces or []
        self.project_evidence_rows = project_evidence or []
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

    def project_evidence(self, **_bounds: object) -> list[ProjectEvidenceRow]:
        return self.project_evidence_rows

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


def activate_test_generation(connection: Any, *, source_contract_fingerprint: str) -> str:
    generation_id = "generation-test"
    marker = DerivedEvent(
        scope=OPERATIONAL_SCOPE,
        entity_id=generation_id,
        entity_version=1,
        event_sequence=1,
        event_name="introspection.analysis_generation.activated",
        attributes={"analysis.generation": generation_id},
        timestamp_ns=1,
    )
    with connection:
        enqueue_events(connection, [marker])
        connection.execute(
            "UPDATE otlp_outbox SET status = 'delivered', delivered_at = 'now' WHERE event_id = ?",
            (marker.event_id,),
        )
        connection.execute(
            """
            INSERT INTO analysis_generations (
                id, ordinal, window_start_ns, window_end_ns, source_contract_fingerprint,
                detector_contract_hash, normalization_contract_hash, semantic_hash, created_at
            ) VALUES (?, 1, 0, 2, ?, ?, ?, ?, 'now')
            """,
            (
                generation_id,
                source_contract_fingerprint,
                "b" * 64,
                "c" * 64,
                generations._semantic_contract(source_contract_fingerprint)[2],
            ),
        )
        connection.execute(
            """
            INSERT INTO analysis_generation_event_links (generation_id, event_id, role)
            VALUES (?, ?, 'activation')
            """,
            (generation_id, marker.event_id),
        )
        connection.execute(
            """
            INSERT INTO analysis_generation_activations (
                generation_id, activation_event_id, activated_at
            ) VALUES (?, ?, 'now')
            """,
            (generation_id, marker.event_id),
        )
        connection.execute(
            """
            INSERT INTO analysis_generation_current (
                singleton, generation_id, activation_event_id, activated_at
            ) VALUES (1, ?, ?, 'now')
            """,
            (generation_id, marker.event_id),
        )
    return generation_id


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


def test_session_context_correlation_accepts_each_supported_producer(
    scan_environment: tuple[Any, AppConfig], monkeypatch: pytest.MonkeyPatch
) -> None:
    connection, _config = scan_environment
    now = datetime(2026, 7, 10, 12, tzinfo=UTC)
    source_started_at = now + timedelta(seconds=5)
    source_ended_at = now + timedelta(seconds=10)
    calls: list[dict[str, object]] = []

    def capture(_connection: Any, **kwargs: object) -> None:
        calls.append(kwargs)
        return None

    monkeypatch.setattr(scan, "correlated_project", capture)
    producers = ("codex-cli", "codex-app-server", "omp", "claude-code")
    for producer in producers:
        trace = TraceRow(
            trace_id=f"{producer}-session",
            turn_id=None,
            thread_id=None,
            project_id=None,
            project_name=None,
            project_root=None,
            project_kind=None,
            started_at=now,
            ended_at=now + timedelta(seconds=30),
            total_tokens=0,
            tool_calls=0,
            producer=producer,
            session_id="session-1",
            source_correlation_started_at=source_started_at,
            source_correlation_ended_at=source_ended_at,
        )
        assert scan._correlated_trace_project(connection, trace) is None
    assert calls == [
        {
            "producer": producer,
            "session_id": "session-1",
            "started_at": source_started_at,
            "ended_at": source_ended_at,
        }
        for producer in producers
    ]


def test_session_context_correlation_rejects_missing_mixed_and_duplicate_sessions(
    scan_environment: tuple[Any, AppConfig], monkeypatch: pytest.MonkeyPatch
) -> None:
    connection, _config = scan_environment
    now = datetime(2026, 7, 10, 12, tzinfo=UTC)
    calls: list[dict[str, object]] = []

    def capture(_connection: Any, **kwargs: object) -> None:
        calls.append(kwargs)
        return None

    monkeypatch.setattr(scan, "correlated_project", capture)
    rejected_traces = (
        TraceRow(
            trace_id="missing-session",
            turn_id=None,
            thread_id=None,
            project_id=None,
            project_name=None,
            project_root=None,
            project_kind=None,
            started_at=now,
            ended_at=now + timedelta(seconds=30),
            total_tokens=0,
            tool_calls=0,
            producer="omp",
        ),
        TraceRow(
            trace_id="mixed-producer-sessions",
            turn_id=None,
            thread_id=None,
            project_id=None,
            project_name=None,
            project_root=None,
            project_kind=None,
            started_at=now,
            ended_at=now + timedelta(seconds=30),
            total_tokens=0,
            tool_calls=0,
        ),
        TraceRow(
            trace_id="duplicate-producer-sessions",
            turn_id=None,
            thread_id=None,
            project_id=None,
            project_name=None,
            project_root=None,
            project_kind=None,
            started_at=now,
            ended_at=now + timedelta(seconds=30),
            total_tokens=0,
            tool_calls=0,
        ),
    )
    for trace in rejected_traces:
        assert scan._correlated_trace_project(connection, trace) is None
    assert calls == []


def test_valid_no_data_and_each_single_source_scan(scan_environment: tuple[Any, AppConfig]) -> None:
    connection, config = scan_environment
    now = datetime(2026, 7, 10, 12, tzinfo=UTC)
    empty = FakeSource()
    fingerprint = approve(connection, empty)
    unavailable = run_scan(connection, config, client=empty, end_time=now)
    assert unavailable["status"] == "failed"
    snapshot = next(
        json.loads(row[0])
        for row in connection.execute("SELECT payload_json FROM otlp_outbox")
        if json.loads(row[0])["event.name"] == "introspection.pipeline.snapshot"
    )
    assert snapshot["event.name"] == "introspection.pipeline.snapshot"
    assert snapshot["pipeline.error_class"] == "generation_unavailable"
    assert connection.execute(
        "SELECT error_code FROM scan_runs WHERE id = ?", (unavailable["scan_run_id"],)
    ).fetchone() == ("generation_unavailable",)
    activate_test_generation(connection, source_contract_fingerprint=fingerprint)
    second = run_scan(connection, config, client=empty, end_time=now + timedelta(seconds=1))
    assert second["status"] == "no_data"
    terminal_times = connection.execute(
        "SELECT started_at, completed_at FROM scan_runs WHERE id = ?", (second["scan_run_id"],)
    ).fetchone()
    assert terminal_times is not None
    assert datetime.fromisoformat(str(terminal_times[1])) >= datetime.fromisoformat(
        str(terminal_times[0])
    )

    trace_only = FakeSource(
        traces=[
            TraceRow(
                "trace-1",
                "turn-1",
                "thread-1",
                None,
                None,
                None,
                None,
                now - timedelta(seconds=1),
                now,
                10,
                0,
            )
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
    observation_payload = json.loads(
        connection.execute(
            """
            SELECT payload_json FROM otlp_outbox
            WHERE json_extract(payload_json, '$."event.name"')
                = 'introspection.observation.detected'
            ORDER BY created_at DESC LIMIT 1
            """
        ).fetchone()[0]
    )
    membership = connection.execute(
        """
        SELECT finding_id FROM finding_membership
        WHERE observation_id = ?
        """,
        (observation_payload["entity.id"],),
    ).fetchone()
    assert membership is not None
    assert observation_payload["finding.id"] == membership[0]


def test_attribution_reanalysis_creates_isolated_facts_without_legacy_mutation(
    scan_environment: tuple[Any, AppConfig],
) -> None:
    connection, config = scan_environment
    end = datetime(2026, 7, 10, 12, tzinfo=UTC)
    start = end - timedelta(hours=1)
    source = FakeSource(
        logs=[log_row("log-1", int((end - timedelta(minutes=1)).timestamp() * 1e9))]
    )
    fingerprint = approve(connection, source)
    result = reanalyse_attribution(
        connection,
        config,
        start_time=start,
        end_time=end,
        source_contract_fingerprint=fingerprint,
        client=source,
    )
    assert result["observations"] == 1
    assert connection.execute("SELECT COUNT(*) FROM observations").fetchone() == (0,)
    assert connection.execute("SELECT COUNT(*) FROM findings").fetchone() == (0,)
    assert connection.execute("SELECT COUNT(*) FROM source_watermarks").fetchone() == (0,)
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM attribution_reanalysis_facts WHERE fact_set_id = ?",
            (result["fact_set_id"],),
        ).fetchone()[0]
        >= 4
    )
    finding_payload = json.loads(
        connection.execute(
            """
            SELECT payload_json FROM attribution_reanalysis_facts
            WHERE fact_set_id = ? AND fact_kind = 'finding'
            """,
            (result["fact_set_id"],),
        ).fetchone()[0]
    )
    assert finding_payload["trend_state"] == "isolated"
    assert finding_payload["occurrence_count"] == 1


def test_detector_events_use_direct_and_closed_interval_workspace_evidence(
    tmp_path: Path,
) -> None:
    project = ProjectIdentity("git", tmp_path, "a" * 64, "project")
    first = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    last = first + timedelta(seconds=20)
    evidence = ProjectEvidence(
        direct=(
            DirectProjectEvidence(
                "first",
                "codex-cli",
                "conversation",
                first,
                tmp_path,
                project,
            ),
            DirectProjectEvidence(
                "last",
                "codex-cli",
                "conversation",
                last,
                tmp_path,
                project,
            ),
        ),
        intervals=(
            ConversationProjectInterval(
                "codex-cli",
                "conversation",
                first,
                last,
                project,
            ),
        ),
    )
    rows = [
        log_row(
            identifier,
            int(timestamp.timestamp() * 1_000_000_000),
            conversation_id="conversation",
            producer="codex-cli",
        )
        for identifier, timestamp in (
            ("before", first - timedelta(microseconds=1)),
            ("first", first),
            ("middle", first + timedelta(seconds=10)),
            ("last", last),
            ("after", last + timedelta(microseconds=1)),
        )
    ]

    events, projects = _detector_events(rows, [], [], evidence)
    by_id = {event.event_id: event for event in events}

    assert by_id["before"].project_id.startswith("unresolved:")
    assert by_id["first"].attribution_method == "git_validated_tool_workspace"
    assert by_id["middle"].attribution_method == "git_validated_tool_workspace_interval"
    assert by_id["last"].attribution_method == "git_validated_tool_workspace"
    assert by_id["after"].project_id.startswith("unresolved:")
    assert set(projects) == {project.identity}


def test_reanalysis_persists_idempotent_immutable_workspace_intervals(
    scan_environment: tuple[Any, AppConfig],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    connection, config = scan_environment
    project_root = tmp_path / "project"
    project_root.mkdir()
    project = ProjectIdentity("git", project_root, "b" * 64, "project")
    end = datetime(2026, 7, 10, 12, tzinfo=UTC)
    start = end - timedelta(hours=1)
    first = end - timedelta(minutes=2)
    last = end - timedelta(minutes=1)
    evidence = ProjectEvidence(
        direct=(
            DirectProjectEvidence(
                "first",
                "codex-app-server",
                "conversation",
                first,
                project_root,
                project,
            ),
            DirectProjectEvidence(
                "last",
                "codex-app-server",
                "conversation",
                last,
                project_root,
                project,
            ),
        ),
        intervals=(
            ConversationProjectInterval(
                "codex-app-server",
                "conversation",
                first,
                last,
                project,
            ),
        ),
    )
    source = FakeSource(
        logs=[
            log_row(
                identifier,
                int(timestamp.timestamp() * 1_000_000_000),
                conversation_id="conversation",
                producer="codex-app-server",
            )
            for identifier, timestamp in (
                ("first", first),
                ("middle", first + timedelta(seconds=10)),
                ("last", last),
            )
        ]
    )
    monkeypatch.setattr(scan, "_build_project_evidence", lambda *_args, **_kwargs: evidence)
    fingerprint = approve(connection, source)

    for _ in range(2):
        result = reanalyse_attribution(
            connection,
            config,
            start_time=start,
            end_time=end,
            source_contract_fingerprint=fingerprint,
            client=source,
        )

    observation_payloads = [
        json.loads(str(row[0]))
        for row in connection.execute(
            """
            SELECT payload_json FROM attribution_reanalysis_facts
            WHERE fact_set_id = ? AND fact_kind = 'observation'
            """,
            (result["fact_set_id"],),
        )
    ]
    assert observation_payloads
    assert {payload["project_id"] for payload in observation_payloads} == {project.identity}
    assert connection.execute("SELECT COUNT(*) FROM project_evidence_intervals").fetchone() == (1,)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute("UPDATE project_evidence_intervals SET anchor_count = 3")


def test_attribution_reanalysis_deduplicates_repeated_source_events(
    scan_environment: tuple[Any, AppConfig],
) -> None:
    connection, config = scan_environment
    end = datetime(2026, 7, 10, 12, tzinfo=UTC)
    start = end - timedelta(hours=1)
    repeated = log_row("log-1", int((end - timedelta(minutes=1)).timestamp() * 1e9))
    source = FakeSource(logs=[repeated, repeated])
    fingerprint = approve(connection, source)

    result = reanalyse_attribution(
        connection,
        config,
        start_time=start,
        end_time=end,
        source_contract_fingerprint=fingerprint,
        client=source,
    )

    fact_ids = [
        row[0]
        for row in connection.execute(
            "SELECT id FROM attribution_reanalysis_facts WHERE fact_set_id = ?",
            (result["fact_set_id"],),
        )
    ]
    assert result["observations"] > 0
    assert result["facts"] == len(fact_ids)
    assert len(fact_ids) == len(set(fact_ids))


def test_attribution_reanalysis_scopes_facts_to_each_immutable_fact_set(
    scan_environment: tuple[Any, AppConfig],
) -> None:
    connection, config = scan_environment
    end = datetime(2026, 7, 10, 12, tzinfo=UTC)
    start = end - timedelta(hours=1)
    source = FakeSource(
        logs=[log_row("log-1", int((end - timedelta(minutes=1)).timestamp() * 1e9))]
    )
    fingerprint = approve(connection, source)

    first = reanalyse_attribution(
        connection,
        config,
        start_time=start,
        end_time=end,
        source_contract_fingerprint=fingerprint,
        client=source,
    )
    second = reanalyse_attribution(
        connection,
        config,
        start_time=start,
        end_time=end,
        source_contract_fingerprint=fingerprint,
        client=source,
    )

    first_ids = {
        row[0]
        for row in connection.execute(
            "SELECT id FROM attribution_reanalysis_facts WHERE fact_set_id = ?",
            (first["fact_set_id"],),
        )
    }
    second_ids = {
        row[0]
        for row in connection.execute(
            "SELECT id FROM attribution_reanalysis_facts WHERE fact_set_id = ?",
            (second["fact_set_id"],),
        )
    }
    assert first_ids
    assert second_ids
    assert first_ids.isdisjoint(second_ids)


def test_new_scan_recovers_interrupted_runs_after_acquiring_the_lease(
    scan_environment: tuple[Any, AppConfig],
) -> None:
    connection, config = scan_environment
    now = datetime(2026, 7, 10, 12, tzinfo=UTC)
    source = FakeSource()
    fingerprint = approve(connection, source)
    activate_test_generation(connection, source_contract_fingerprint=fingerprint)
    connection.execute(
        """
        INSERT INTO scan_runs (id, status, started_at, details_json)
        VALUES ('interrupted-run', 'running', '2026-07-10T11:00:00+00:00', '{}')
        """
    )
    connection.commit()

    result = run_scan(connection, config, client=source, end_time=now)

    assert result["status"] == "no_data"
    assert result["recovered_interrupted_scan_runs"] == 1
    assert connection.execute(
        "SELECT status, error_code FROM scan_runs WHERE id = 'interrupted-run'"
    ).fetchone() == ("failed", "interrupted")


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
    fingerprint = approve(connection, source)
    activate_test_generation(connection, source_contract_fingerprint=fingerprint)
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
    fingerprint = approve(connection, source)
    activate_test_generation(connection, source_contract_fingerprint=fingerprint)
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
            "project-1",
            "Project",
            project.as_posix(),
            "non_git",
            timestamp - timedelta(seconds=1),
            timestamp,
            0,
            0,
        )
        for index, timestamp in enumerate(occurred_at)
    ]
    source = FakeSource(logs=rows, traces=traces)
    fingerprint = approve(connection, source)
    activate_test_generation(connection, source_contract_fingerprint=fingerprint)

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
    fingerprint = approve(connection, source)
    activate_test_generation(connection, source_contract_fingerprint=fingerprint)

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
    payloads = {
        json.loads(row[0])["event.name"]
        for row in connection.execute("SELECT payload_json FROM otlp_outbox").fetchall()
    }
    assert "introspection.pipeline.snapshot" in payloads
    assert "introspection.review.activity_snapshot" not in payloads


def _assert_failed_extraction_has_no_analytics(connection: Any, error_class: str) -> None:
    assert connection.execute("SELECT COUNT(*) FROM observations").fetchone() == (0,)
    assert connection.execute("SELECT COUNT(*) FROM evidence").fetchone() == (0,)
    assert connection.execute("SELECT COUNT(*) FROM finding_membership").fetchone() == (0,)
    assert connection.execute("SELECT COUNT(*) FROM source_watermarks").fetchone() == (0,)
    payloads = [
        json.loads(row[0])
        for row in connection.execute("SELECT payload_json FROM otlp_outbox").fetchall()
    ]
    pipeline = next(
        payload
        for payload in payloads
        if payload["event.name"] == "introspection.pipeline.snapshot"
    )
    assert pipeline["scan.terminal_status"] == "failed"
    assert pipeline["pipeline.error_class"] == error_class
    assert "analysis.generation" not in pipeline
    names = {payload["event.name"] for payload in payloads}
    assert "introspection.pipeline.snapshot" in names
    assert "introspection.review.activity_snapshot" not in names
    assert not names & {
        "introspection.observation.detected",
        "introspection.trend.evaluated",
        "introspection.trend.promoted",
    }


def test_source_contract_failure_persists_only_safe_terminal_facts(
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
    _assert_failed_extraction_has_no_analytics(connection, "source_contract")


def test_scan_deadline_terminalizes_stalled_work_without_retaining_the_lease(
    scan_environment: tuple[Any, AppConfig], monkeypatch: pytest.MonkeyPatch
) -> None:
    connection, config = scan_environment
    now = datetime(2026, 7, 10, 12, tzinfo=UTC)

    class SlowSource(FakeSource):
        def logs(self, **_bounds: object) -> list[LogRow]:
            time.sleep(0.05)
            return []

    source = SlowSource()
    fingerprint = approve(connection, source)
    activate_test_generation(connection, source_contract_fingerprint=fingerprint)
    monkeypatch.setattr(scan, "_SCAN_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(scan.ScanDeadlineExceeded, match="scan exceeded 0 second deadline"):
        run_scan(connection, config, client=source, end_time=now)

    pipeline = next(
        json.loads(row[0])
        for row in connection.execute("SELECT payload_json FROM otlp_outbox")
        if json.loads(row[0])["event.name"] == "introspection.pipeline.snapshot"
    )
    assert pipeline["scan.terminal_status"] == "failed"
    assert pipeline["pipeline.error_class"] == "scan_timeout"
    assert connection.execute("SELECT COUNT(*) FROM source_watermarks").fetchone() == (0,)
    assert connection.execute("SELECT status, error_code FROM scan_runs").fetchone() == (
        "failed",
        "ScanDeadlineExceeded",
    )
    assert connection.execute("SELECT COUNT(*) FROM scheduler_leases").fetchone() == (0,)


def test_scan_deadline_setup_failure_does_not_retain_a_lease(
    scan_environment: tuple[Any, AppConfig], monkeypatch: pytest.MonkeyPatch
) -> None:
    connection, config = scan_environment

    def fail_to_arm() -> tuple[Any, tuple[float, float]]:
        raise scan.ScanError("scan deadline timer is already active")

    monkeypatch.setattr(scan, "_arm_scan_deadline", fail_to_arm)
    with pytest.raises(scan.ScanError, match="timer is already active"):
        run_scan(connection, config, client=FakeSource())

    assert connection.execute("SELECT COUNT(*) FROM scheduler_leases").fetchone() == (0,)


def test_delivery_stage_timeout_persists_a_terminal_failure_and_releases_the_lease(
    scan_environment: tuple[Any, AppConfig], monkeypatch: pytest.MonkeyPatch
) -> None:
    connection, config = scan_environment
    now = datetime(2026, 7, 10, 12, tzinfo=UTC)
    source = FakeSource()
    fingerprint = approve(connection, source)
    activate_test_generation(connection, source_contract_fingerprint=fingerprint)

    def timeout_delivery(*_args: object, **_kwargs: object) -> dict[str, int]:
        raise scan.ScanDeadlineExceeded("scan exceeded 900 second deadline")

    monkeypatch.setattr(scan, "drain_outbox", timeout_delivery)
    with pytest.raises(scan.ScanDeadlineExceeded, match="scan exceeded 900 second deadline"):
        run_scan(connection, config, client=source, end_time=now)

    pipeline = next(
        json.loads(row[0])
        for row in connection.execute("SELECT payload_json FROM otlp_outbox")
        if json.loads(row[0])["event.name"] == "introspection.pipeline.snapshot"
    )
    assert pipeline["scan.terminal_status"] == "failed"
    assert pipeline["pipeline.error_class"] == "scan_timeout"
    assert connection.execute("SELECT COUNT(*) FROM source_watermarks").fetchone() == (1,)
    terminal = connection.execute(
        "SELECT status, completed_at, error_code FROM scan_runs"
    ).fetchone()
    assert terminal is not None
    assert terminal[0] == "failed"
    assert terminal[1] is not None
    assert terminal[2] == "ScanDeadlineExceeded"
    assert connection.execute("SELECT COUNT(*) FROM scheduler_leases").fetchone() == (0,)


def test_trace_and_hydration_failures_persist_no_analytics_or_projections(
    scan_environment: tuple[Any, AppConfig],
) -> None:
    connection, config = scan_environment
    now = datetime(2026, 7, 10, 12, tzinfo=UTC)

    class TraceFailure(FakeSource):
        def traces(self, **_bounds: object) -> list[TraceRow]:
            raise SourceError("trace query unavailable")

    trace_failure = TraceFailure(logs=[log_row("log-1", int(now.timestamp() * 1_000_000_000))])
    fingerprint = approve(connection, trace_failure)
    activate_test_generation(connection, source_contract_fingerprint=fingerprint)
    with pytest.raises(SourceError, match="trace query"):
        run_scan(connection, config, client=trace_failure, end_time=now)
    _assert_failed_extraction_has_no_analytics(connection, "traces_query")

    connection.close()
    hydration_path = config.database.path.with_name("hydration.sqlite3")
    connection = connect_database(hydration_path)
    config = AppConfig(database=DatabaseConfig(path=hydration_path))

    class HydrationFailure(FakeSource):
        def hydrate(self, **_bounds: object) -> list[HydrationRow]:
            raise SourceError("hydration unavailable")

    hydration_failure = HydrationFailure(
        logs=[log_row("log-2", int(now.timestamp() * 1_000_000_000))]
    )
    fingerprint = approve(connection, hydration_failure)
    activate_test_generation(connection, source_contract_fingerprint=fingerprint)
    with pytest.raises(SourceError, match="hydration"):
        run_scan(connection, config, client=hydration_failure, end_time=now)
    _assert_failed_extraction_has_no_analytics(connection, "hydration")
    connection.close()


def test_generation_contract_mismatch_stops_before_extraction(
    scan_environment: tuple[Any, AppConfig], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection, config = scan_environment
    now = datetime(2026, 7, 10, 12, tzinfo=UTC)
    source = FakeSource(logs=[log_row("unread", int(now.timestamp() * 1_000_000_000))])
    fingerprint = approve(connection, source)
    activate_test_generation(connection, source_contract_fingerprint=fingerprint)
    changed_source = tmp_path / "source.py"
    changed_source.write_text("changed source extraction contract\n")
    monkeypatch.setattr(generations.source, "__file__", str(changed_source))
    validate_contract = scan.validate_active_generation_contract

    def validate_before_scan_persistence(*args: object, **kwargs: object) -> str | None:
        assert connection.execute("SELECT COUNT(*) FROM scan_runs").fetchone() == (0,)
        return validate_contract(*args, **kwargs)

    monkeypatch.setattr(
        scan, "validate_active_generation_contract", validate_before_scan_persistence
    )

    with pytest.raises(GenerationError, match="semantic contract is incompatible"):
        run_scan(connection, config, client=source, end_time=now)

    assert source.log_reads == 0
    assert source.trace_reads == 0
    _assert_failed_extraction_has_no_analytics(connection, "generation_contract")


def test_active_generation_source_contract_mismatch_stops_before_extraction(
    scan_environment: tuple[Any, AppConfig],
) -> None:
    connection, config = scan_environment
    now = datetime(2026, 7, 10, 12, tzinfo=UTC)
    source = FakeSource(logs=[log_row("unread", int(now.timestamp() * 1_000_000_000))])
    fingerprint = approve(connection, source)
    activate_test_generation(connection, source_contract_fingerprint=fingerprint)

    class ChangedSource(FakeSource):
        def query(self, sql: str, parameters: object) -> list[dict[str, Any]]:
            rows = super().query(sql, parameters)
            if "system.columns" not in sql:
                return rows
            changed = [dict(row) for row in rows]
            changed[0]["type"] = "Array(String)"
            return changed

    changed_source = ChangedSource(logs=source.log_rows)
    assert approve(connection, changed_source) != fingerprint

    with pytest.raises(GenerationError, match="source contract is incompatible"):
        run_scan(connection, config, client=changed_source, end_time=now)

    assert changed_source.log_reads == 0
    assert changed_source.trace_reads == 0
    _assert_failed_extraction_has_no_analytics(connection, "generation_contract")


def test_pipeline_snapshot_uses_source_timestamps_and_preserves_no_data_semantics() -> None:
    event = _pipeline_snapshot_event(
        scan_run_id="scan-1",
        end_ns=10_000_000_000,
        terminal_status="succeeded",
        error_class=None,
        logs=PipelineStream("available", "records", 9_000_000_000),
        traces=PipelineStream("available", "records", 8_000_000_000),
        hydration=PipelineStream("available", "no_data"),
        finished_ns=10_000_000_000,
        duration_ms=3.0,
        rows_processed=2,
        pending_after_drain=0,
        active_generation="generation-1",
    )
    assert event.attributes["logs.lag_ms"] == 1_000
    assert event.attributes["traces.lag_ms"] == 2_000
    assert event.attributes["scan.duration_ms"] == 3.0

    no_data = _pipeline_snapshot_event(
        scan_run_id="scan-2",
        end_ns=10_000_000_000,
        terminal_status="no_data",
        error_class=None,
        logs=PipelineStream("available", "no_data"),
        traces=PipelineStream("available", "no_data"),
        hydration=PipelineStream("available", "no_data"),
        finished_ns=10_000_000_000,
        duration_ms=3.0,
        rows_processed=0,
        pending_after_drain=0,
        active_generation="generation-1",
    )
    assert no_data.attributes["pipeline.state"] == "healthy"
    assert no_data.attributes["logs.lag_state"] == "not_applicable"
    assert no_data.attributes["traces.lag_state"] == "not_applicable"
    assert "logs.lag_ms" not in no_data.attributes
    assert "traces.lag_ms" not in no_data.attributes

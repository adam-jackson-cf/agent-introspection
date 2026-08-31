from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agent_introspection.source import (
    LOG_QUERY,
    RAW_SOURCE_SESSION_LOG_QUERY,
    RAW_SOURCE_SESSION_METRIC_QUERY,
    RAW_SOURCE_SESSION_TRACE_QUERY,
    RAW_SOURCE_WINDOW_ANCHOR_QUERY,
    TRACE_QUERY,
    ClickHouseClient,
    HydrationRequest,
    SourceError,
    parse_duration_ms,
    parse_log_row,
    parse_source_activity_correlation,
    parse_source_session_row,
    parse_trace_row,
    query_selected_ids,
)


def test_broad_queries_enumerate_all_services_in_a_half_open_window() -> None:
    assert "timestamp >= {start_ns:UInt64}" in LOG_QUERY
    assert "timestamp < {end_ns:UInt64}" in LOG_QUERY
    assert "ts_bucket_start BETWEEN {start_bucket:UInt64} AND {end_bucket:UInt64}" in LOG_QUERY
    assert "resource.`service.name`::String AS service_name" in LOG_QUERY
    assert "AS producer" not in LOG_QUERY
    assert "attributes_string['duration_ms']" in LOG_QUERY
    assert "AS duration_ms" in LOG_QUERY
    assert "attributes_number['duration_ms']" not in LOG_QUERY
    for raw_key in ("prompt", "arguments", "output", "error.message", "body"):
        assert f"['{raw_key}']" not in LOG_QUERY

    assert "timestamp >= {start:DateTime64(9)}" in TRACE_QUERY
    assert "timestamp < {end:DateTime64(9)}" in TRACE_QUERY
    assert "ts_bucket_start BETWEEN {start_bucket:UInt64} AND {end_bucket:UInt64}" in TRACE_QUERY
    assert "serviceName IN" not in TRACE_QUERY
    assert "groupUniqArray(serviceName)" in TRACE_QUERY
    assert "arraySort(groupUniqArray(spanID)) AS source_span_ids" in TRACE_QUERY
    for field in (
        "turn_ids",
        "legacy_turn_ids",
        "thread_ids",
        "legacy_thread_ids",
        "conversation_ids",
        "gen_ai_conversation_ids",
    ):
        assert f"AS {field}" in TRACE_QUERY


def test_raw_source_session_queries_scope_canonical_services_in_a_half_open_window() -> None:
    for query, start, end, service_expression in (
        (
            RAW_SOURCE_SESSION_LOG_QUERY,
            "{start_ns:UInt64}",
            "{end_ns:UInt64}",
            "resource.`service.name`::String IN",
        ),
        (
            RAW_SOURCE_SESSION_TRACE_QUERY,
            "{start:DateTime64(9)}",
            "{end:DateTime64(9)}",
            "serviceName IN",
        ),
    ):
        assert f"timestamp >= {start}" in query
        assert f"timestamp < {end}" in query
        assert service_expression in query
        for service_name in (
            "codex-cli",
            "claude-code",
            "omp",
            "codex_exec",
            "codex_cli_rs",
            "codex-app-server",
            "oh-my-pi",
        ):
            assert f"'{service_name}'" in query
        for native_key in (
            "session.id",
            "thread.id",
            "thread_id",
            "gen_ai.conversation.id",
        ):
            assert f"mapContains(attributes_string, '{native_key}')" in query
        assert "mapContains(attributes_string, 'thread.id')" in query
        assert "'oh-my-pi')" in query
        assert "mapContains(attributes_string, 'gen_ai.conversation.id')" in query
        assert "GROUP BY service_name, source_id" in query
        assert "session_ids" in query
        assert "thread_ids" in query
        assert "legacy_thread_ids" in query
        assert "gen_ai_conversation_ids" in query

        assert "ts_bucket_start BETWEEN {start_bucket:UInt64} AND {end_bucket:UInt64}" in query

    assert "inserted_at_unix_milli >= {start_ms:Int64}" in RAW_SOURCE_SESSION_METRIC_QUERY
    assert "inserted_at_unix_milli < {end_ms:Int64}" in RAW_SOURCE_SESSION_METRIC_QUERY
    assert "metric_name = 'claude_code.session.count'" in RAW_SOURCE_SESSION_METRIC_QUERY
    assert "attrs['service.name'] = 'claude-code'" in RAW_SOURCE_SESSION_METRIC_QUERY
    assert "mapContains(attrs, 'session.id')" in RAW_SOURCE_SESSION_METRIC_QUERY
    assert "GROUP BY service_name, source_id" in RAW_SOURCE_SESSION_METRIC_QUERY


def test_metric_source_session_uses_arrival_timestamp_and_claude_session_id() -> None:
    row = parse_source_session_row(
        {
            "source_id": "fingerprint",
            "source_timestamp_ms": 1_767_225_600_123,
            "service_name": "claude-code",
            "session_ids": ["claude-session"],
            "thread_ids": [],
            "legacy_thread_ids": [],
            "gen_ai_conversation_ids": [],
        },
        source_kind="metric",
    )

    assert row.source_kind == "metric"
    assert row.source_timestamp == datetime.fromtimestamp(1_767_225_600.123, tz=UTC)
    assert row.native_session_ids == ("claude-session",)


def test_raw_source_window_anchor_converts_normal_datetime64_output() -> None:
    class AnchorClient(ClickHouseClient):
        def query(self, sql: str, parameters: Mapping[str, str | int]) -> Iterator[dict[str, Any]]:
            assert sql == RAW_SOURCE_WINDOW_ANCHOR_QUERY
            assert parameters == {}
            yield {
                "logs_earliest_ns": 10,
                "traces_earliest_ns": "2026-01-01 00:00:00.000000000",
            }

    client = AnchorClient(docker_context="test")
    assert client.raw_source_window_anchor() == (10, 1_767_225_600_000_000_000)


def test_retained_producer_identity_proofs_are_bounded_and_support_only_proven_surfaces() -> None:
    fixture_path = Path(__file__).with_name("fixtures") / "producer_identity_proofs.json"
    evidence = json.loads(fixture_path.read_text())

    assert evidence["observed_on"] == "2026-08-29"
    assert evidence["evidence_policy"] == {
        "prompts_recorded": False,
        "responses_recorded": False,
        "identifiers_and_counts_only": True,
    }
    supported = {proof["producer"]: proof for proof in evidence["supported"]}
    assert set(supported) == {"omp", "codex-cli", "codex-app-server"}
    required_fields = {
        "version",
        "command_argv_without_prompt",
        "native_lifecycle_field",
        "local_artifact_field",
        "otel_service",
        "otel_field",
        "correlation_id",
        "local_artifact_ids",
        "first_context_at",
        "last_context_at",
        "first_otel_at",
        "last_otel_at",
        "otel_trace_count",
        "otel_span_count",
        "project",
        "scenarios",
    }
    required_scenarios = {
        "fresh",
        "resume",
        "end",
        "concurrent_projects",
        "non_git",
        "workspace_change",
    }
    for proof in supported.values():
        assert required_fields <= proof.keys()
        assert proof["local_artifact_ids"]
        assert proof["otel_trace_count"] > 0
        assert proof["otel_span_count"] > 0
        assert set(proof["project"]) == {"id", "name", "root", "kind"}
        assert set(proof["scenarios"]) == required_scenarios
    assert supported["codex-cli"]["scenarios"] == {
        "fresh": "passed",
        "resume": "passed",
        "end": "not_exposed_by_installed_notify",
        "concurrent_projects": "passed",
        "non_git": "passed",
        "workspace_change": "not_exposed_by_installed_notify",
    }
    assert supported["codex-app-server"]["correlation_id"] == (
        "01a04f2e-fa8f-7c31-9c8e-3693acb033f3"
    )
    assert supported["codex-app-server"]["scenarios"] == {
        "fresh": "passed",
        "resume": "not_exercised",
        "end": "passed",
        "concurrent_projects": "not_exercised",
        "non_git": "not_exercised",
        "workspace_change": "unsupported_by_design",
    }

    unsupported = {proof["producer"]: proof for proof in evidence["unsupported"]}
    assert set(unsupported) == {"claude-code", "codex-app"}
    assert all(proof["source_ingestion_enabled"] is False for proof in unsupported.values())
    serialized = json.dumps(evidence, sort_keys=True).lower()
    for forbidden in ("prompt_text", "response_text", "command_output", "environment_values"):
        assert forbidden not in serialized


def test_trace_query_retains_raw_identity_candidates_without_producer_detection() -> None:
    assert "arrayJoin(" not in TRACE_QUERY
    assert "native_correlation_id" not in TRACE_QUERY
    assert "AS correlation_status" not in TRACE_QUERY
    assert "AS correlation_id" not in TRACE_QUERY
    assert "AS producer" not in TRACE_QUERY
    for field in (
        "attributes_string['thread.id']",
        "attributes_string['thread_id']",
        "attributes_string['gen_ai.conversation.id']",
    ):
        assert field in TRACE_QUERY


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ("", None), ("0", 0.0), ("12.5", 12.5)],
)
def test_duration_parser_accepts_only_installed_decimal_string_shape(
    value: object, expected: float | None
) -> None:
    assert parse_duration_ms(value) == expected


@pytest.mark.parametrize(
    "value",
    [-1, 1.5, True, "-1", "+1", "01", "1e3", "nan", " 10", "10ms", "\u0661\u0660"],
)
def test_duration_parser_fails_closed_on_ambiguous_values(value: object) -> None:
    with pytest.raises(SourceError):
        parse_duration_ms(value)


def test_log_parser_preserves_missing_boolean_instead_of_treating_it_as_false() -> None:
    base: dict[str, object] = {
        "timestamp": 1,
        "id": "log-1",
        "event_name": "codex.api_request",
        "success_bool": None,
        "duration_ms": "4.25",
    }
    parsed = parse_log_row(base)
    assert parsed.success_bool is None
    assert parsed.duration_ms == 4.25
    base["success_bool"] = False
    assert parse_log_row(base).success_bool is False


def test_log_parser_accepts_clickhouse_json_64_bit_integer_text() -> None:
    parsed = parse_log_row(
        {
            "timestamp": "1783695231067293000",
            "id": "log-1",
            "event_name": "codex.api_request",
            "status_code": "429",
            "input_tokens": "123",
        }
    )
    assert parsed.timestamp_ns == 1_783_695_231_067_293_000
    assert parsed.status_code == 429
    assert parsed.input_tokens == 123
    with pytest.raises(SourceError, match="unsigned integer text"):
        parse_log_row({"timestamp": "+1", "id": "log-1", "event_name": "codex.api_request"})

    assert parsed.producer is None


def test_log_parser_retains_tool_rows_without_event_name() -> None:
    parsed = parse_log_row({"timestamp": "1", "id": "log-1", "tool_name": "exec_command"})
    assert parsed.event_name == ""
    assert parsed.tool_name == "exec_command"


def test_trace_parser_interprets_installed_clickhouse_naive_datetime_as_utc() -> None:
    parsed = parse_trace_row(
        {
            "trace_id": "trace-1",
            "started_at": "2026-07-10 14:53:47.565735000",
            "ended_at": "2026-07-10 14:53:48.565735000",
            "source_span_ids": ["span-1", "span-2"],
            "service_names": ["other-service"],
            "thread_ids": [],
            "legacy_thread_ids": [],
            "turn_ids": [],
            "legacy_turn_ids": [],
            "conversation_ids": [],
            "gen_ai_conversation_ids": [],
            "total_tokens": "123",
            "tool_calls": "2",
        }
    )
    assert parsed.started_at.tzinfo is UTC
    assert parsed.ended_at.tzinfo is UTC
    assert parsed.source_span_ids == ("span-1", "span-2")
    assert parsed.service_names == ("other-service",)
    assert parsed.thread_id is None
    assert parsed.correlation is None


@pytest.mark.parametrize(
    ("producer", "producer_surface"),
    [
        ("codex-cli", "codex-cli"),
        ("codex-app-server", "codex-app-server"),
        ("omp", "omp"),
    ],
)
def test_trace_parser_returns_canonical_source_activity_correlation(
    producer: str, producer_surface: str
) -> None:
    parsed = parse_trace_row(
        {
            "trace_id": "trace-1",
            "started_at": "2026-07-10 14:53:47.565735000",
            "ended_at": "2026-07-10 14:53:48.565735000",
            "producer": producer,
            "producer_surface": producer_surface,
            "correlation_id": "conversation-1",
            "source_event_timestamp": "2026-07-10 14:53:47.765735000",
            "source_span_ids": ["span-1"],
            "total_tokens": "123",
            "tool_calls": "2",
        }
    )
    assert parsed.correlation is not None
    assert parsed.correlation.producer == producer
    assert parsed.correlation.producer_surface == producer_surface
    assert parsed.correlation.correlation_id == "conversation-1"
    assert parsed.correlation.source_event_timestamp == datetime(
        2026, 7, 10, 14, 53, 47, 765735, tzinfo=UTC
    )
    assert parsed.correlation.source_span_ids == ("span-1",)


@pytest.mark.parametrize(
    ("state", "correlation_id"),
    [("missing", None), ("conflicting", None)],
)
def test_trace_parser_exposes_rejected_correlation_status(state: str, correlation_id: None) -> None:
    parsed = parse_trace_row(
        {
            "trace_id": "trace-1",
            "started_at": "2026-07-10 14:53:47.565735000",
            "ended_at": "2026-07-10 14:53:48.565735000",
            "correlation_status": state,
            "producer": "codex-cli",
            "producer_surface": "codex-cli",
            "correlation_id": correlation_id,
            "source_event_timestamp": "2026-07-10 14:53:47.765735000",
            "source_span_ids": ["span-1"],
            "total_tokens": "123",
            "tool_calls": "2",
        }
    )
    assert parsed.correlation is None
    assert parsed.correlation_status is not None
    assert parsed.correlation_status.state == state
    assert parsed.correlation_status.producer == "codex-cli"
    assert parsed.correlation_status.source_span_ids == ("span-1",)


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"correlation_id": None}, "fields must be present together"),
        ({"correlation_id": ["thread-a", "thread-b"]}, "optional text value"),
        ({"producer_surface": "codex-app-server"}, "producer surface is invalid"),
        ({"source_span_ids": ["span-2", "span-1"]}, "sorted unique"),
    ],
)
def test_source_activity_correlation_fails_closed(change: dict[str, object], error: str) -> None:
    data: dict[str, object] = {
        "producer": "codex-cli",
        "producer_surface": "codex-cli",
        "correlation_id": "thread-1",
        "source_event_timestamp": "2026-07-10 14:53:47.765735000",
        "source_span_ids": ["span-1"],
    }
    data.update(change)
    with pytest.raises(SourceError, match=error):
        parse_source_activity_correlation(data)


def test_client_requires_exact_parameter_set_and_uses_clickhouse_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, '{"value": 1}\n', "")

    monkeypatch.setattr(subprocess, "run", run)
    client = ClickHouseClient(docker_context="orbstack")
    rows = list(client.query("SELECT {start:UInt64} AS value", {"start": 7}))
    assert rows == [{"value": 1}]
    assert "--param_start=7" in calls[0]
    assert calls[0][:6] == [
        "docker",
        "--context",
        "orbstack",
        "exec",
        "-i",
        "signoz-clickhouse",
    ]
    with pytest.raises(SourceError, match="parameter mismatch"):
        list(client.query("SELECT {start:UInt64}", {"end": 7}))


def test_client_fails_closed_when_a_query_exceeds_its_bounded_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        timeout_seconds = kwargs["timeout"]
        assert isinstance(timeout_seconds, float)
        assert timeout_seconds == 600.0
        raise subprocess.TimeoutExpired(argv, timeout_seconds)

    monkeypatch.setattr(subprocess, "run", timeout)
    client = ClickHouseClient(docker_context="orbstack")
    with pytest.raises(SourceError, match="ClickHouse query exceeded 600 second timeout"):
        list(client.query("SELECT 1", {}))


def test_client_fails_closed_for_invalid_json_and_clickhouse_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = ClickHouseClient(docker_context="orbstack")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "not-json\n", ""),
    )
    with pytest.raises(SourceError, match="invalid JSONEachRow"):
        list(client.query("SELECT 1", {}))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 2, "", "schema mismatch\n"),
    )
    with pytest.raises(SourceError, match="schema mismatch"):
        list(client.query("SELECT 1", {}))


def test_client_bounds_every_source_window_to_safe_partitions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, str | int]] = []

    def query(_sql: str, parameters: Mapping[str, str | int]) -> Iterator[dict[str, Any]]:
        calls.append(dict(parameters))
        return iter(())

    client = ClickHouseClient(docker_context="orbstack")
    monkeypatch.setattr(client, "query", query)
    start_ns = 2_000_000_000_000
    end_ns = 2_120_000_000_000
    start = datetime.fromtimestamp(start_ns / 1_000_000_000, tz=UTC)
    end = datetime.fromtimestamp(end_ns / 1_000_000_000, tz=UTC)

    assert list(client.logs(start_ns=start_ns, end_ns=end_ns)) == []
    assert list(client.traces(start=start, end=end)) == []
    assert (
        list(client.source_sessions(start=start, end=end, start_ns=start_ns, end_ns=end_ns)) == []
    )
    assert len(calls) == 5
    assert all(
        parameters["start_bucket"] == 200 and parameters["end_bucket"] == 2_120
        for parameters in calls[:4]
    )
    assert calls[-1] == {"start_ms": 2_000_000, "end_ms": 2_120_000}


def test_trace_bounds_must_be_timezone_aware() -> None:
    client = ClickHouseClient(docker_context="orbstack")
    with pytest.raises(ValueError, match="timezone-aware"):
        list(
            client.traces(
                start=datetime(2026, 1, 1),
                end=datetime(2026, 1, 2),
            )
        )
    with pytest.raises(ValueError, match="ordered"):
        list(
            client.traces(
                start=datetime(2026, 1, 2, tzinfo=UTC),
                end=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )


def test_selected_id_hydration_uses_parameters_for_every_identifier() -> None:
    predicate, parameters = query_selected_ids(("log-a", "log-b"))
    assert predicate == "id IN ({id_0:String}, {id_1:String})"
    assert parameters == {"id_0": "log-a", "id_1": "log-b"}
    with pytest.raises(ValueError, match="at least one id is required"):
        query_selected_ids(())


def test_hydration_is_allowlisted_bounded_and_parameterized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        row = '{"timestamp":1,"id":"log-a","event_name":"tool","arguments":"{}"}\n'
        return subprocess.CompletedProcess(argv, 0, row, "")

    monkeypatch.setattr(subprocess, "run", run)
    client = ClickHouseClient(docker_context="orbstack")
    rows = list(
        client.hydrate(
            HydrationRequest(
                identity_kind="call_id",
                identifiers=("call-a",),
                start_ns=1,
                end_ns=2,
                start_bucket=1,
                end_bucket=2,
            )
        )
    )
    assert rows[0].arguments == "{}"
    argv = calls[0]
    assert "--param_identifiers=['call-a']" in argv
    sql = argv[argv.index("--query") + 1]
    assert "attributes_string['call_id'] IN ({identifiers:Array(String)})" in sql
    assert "{start_ns:UInt64}" in sql


def test_hydration_deduplicates_identifiers_without_embedding_them_in_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    client = ClickHouseClient(docker_context="orbstack")
    list(
        client.hydrate(
            HydrationRequest(
                identity_kind="log_id",
                identifiers=("log-a", "log-a", "log' OR 1=1"),
                start_ns=1,
                end_ns=2,
                start_bucket=1,
                end_bucket=2,
            )
        )
    )
    argv = calls[0]
    sql = argv[argv.index("--query") + 1]
    assert "log-a" not in sql
    assert "OR 1=1" not in sql
    assert "--param_identifiers=['log-a','log\\' OR 1=1']" in argv
    with pytest.raises(ValueError, match="non-empty strings"):
        list(
            client.hydrate(
                HydrationRequest(
                    identity_kind="trace_id",
                    identifiers=("",),
                    start_ns=1,
                    end_ns=2,
                    start_bucket=1,
                    end_bucket=2,
                )
            )
        )

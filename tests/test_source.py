from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_introspection.source import (
    LOG_QUERY,
    TRACE_QUERY,
    ClickHouseClient,
    SourceError,
    parse_duration_ms,
    parse_log_row,
    parse_source_activity_correlation,
    parse_trace_row,
    query_selected_ids,
)


def test_broad_queries_are_bounded_and_exclude_raw_content() -> None:
    assert "{start_ns:UInt64}" in LOG_QUERY
    assert "{end_ns:UInt64}" in LOG_QUERY
    assert "{start_bucket:UInt64}" in LOG_QUERY
    assert "{end_bucket:UInt64}" in LOG_QUERY
    assert "attributes_string['duration_ms']" in LOG_QUERY
    assert "AS duration_ms" in LOG_QUERY
    assert "attributes_number['duration_ms']" not in LOG_QUERY
    assert "AS producer" in LOG_QUERY
    for raw_key in ("prompt", "arguments", "output", "error.message", "body"):
        assert f"['{raw_key}']" not in LOG_QUERY
    assert "{start:DateTime64(9)}" in TRACE_QUERY
    assert "{end:DateTime64(9)}" in TRACE_QUERY
    assert "{{start:DateTime64(9)}}" not in TRACE_QUERY
    assert "{{end:DateTime64(9)}}" not in TRACE_QUERY
    assert "attributes_string['thread.id']" in TRACE_QUERY
    assert "attributes_string['thread_id']" in TRACE_QUERY
    assert "attributes_string['gen_ai.conversation.id']" in TRACE_QUERY
    assert "attributes_string['session.id']" in TRACE_QUERY
    assert "attributes_string['sessionId']" not in TRACE_QUERY
    assert "native_correlation_id" in TRACE_QUERY
    assert "uniqExactIf(native_correlation_id, has_native_correlation) = 1" in TRACE_QUERY
    assert "correlation_session_id" not in TRACE_QUERY
    assert "AS correlation_id" in TRACE_QUERY
    assert "AS producer_surface" in TRACE_QUERY
    assert "AS source_event_timestamp" in TRACE_QUERY
    assert "arraySort(groupUniqArray(spanID)) AS source_span_ids" in TRACE_QUERY


def test_retained_producer_identity_proofs_are_bounded_and_support_only_proven_surfaces() -> None:
    fixture_path = Path(__file__).with_name("fixtures") / "producer_identity_proofs.json"
    evidence = json.loads(fixture_path.read_text())

    assert evidence == {
        "observed_on": "2026-08-05",
        "supported": [
            {
                "producer": "omp",
                "version": "17.1.8",
                "correlation_id": "019fd256-2b5b-7000-8243-256d0a07c777",
                "equality_boundary": "local_session_context",
                "otel_span_count": 2,
                "unique_gen_ai_conversation_id_count": 1,
            },
            {
                "producer": "codex-app-server",
                "version": "0.146.0",
                "correlation_id": "019fd25b-c7e2-71d0-9aaf-652dbc1f366b",
                "equality_boundary": "protocol_local_metadata",
                "otel_span_count": 3,
                "isolated_correlation_id": True,
            },
            {
                "producer": "codex-app-server",
                "version": "0.146.0",
                "correlation_id": "019fd25b-d86f-7710-b928-702237161395",
                "equality_boundary": "protocol_local_metadata",
                "otel_span_count": 3,
                "isolated_correlation_id": True,
            },
            {
                "producer": "codex-app-server",
                "version": "0.146.0",
                "correlation_id": "019fd25a-4bea-7181-b8ca-e808d9f1d6e0",
                "resume_preserved": True,
            },
        ],
        "unsupported": [
            {
                "producer": "claude-code",
                "version": "2.1.199",
                "missing_equality_boundary": "local_session_context",
            },
            {
                "producer": "codex-cli",
                "version": "0.146.0",
                "missing_equality_boundary": "protocol_local_metadata",
            },
            {
                "producer": "codex-app",
                "missing_equality_boundary": "protocol_local_metadata",
            },
        ],
    }


def test_trace_query_rejects_missing_multiple_and_conflicting_native_correlations() -> None:
    assert "arrayJoin(" in TRACE_QUERY
    assert "[attributes_string['thread.id'], attributes_string['thread_id']]" in TRACE_QUERY
    assert "native_correlation_id != '' AS has_native_correlation" in TRACE_QUERY
    assert "uniqExactIf(native_correlation_id, has_native_correlation) = 1" in TRACE_QUERY
    assert "anyIf(native_correlation_id, has_native_correlation)" in TRACE_QUERY


@pytest.mark.parametrize("value, expected", [(None, None), ("", None), ("0", 0.0), ("12.5", 12.5)])
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
            "total_tokens": "123",
            "tool_calls": "2",
        }
    )
    assert parsed.started_at.tzinfo is UTC
    assert parsed.ended_at.tzinfo is UTC


@pytest.mark.parametrize(
    ("producer", "producer_surface"),
    [
        ("codex-cli", "codex-cli"),
        ("codex-app-server", "codex-app-server"),
        ("omp", "omp"),
        ("claude-code", "claude-code"),
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
    "change, error",
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
        assert kwargs["timeout"] == 600.0
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

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


def test_trace_bounds_must_be_timezone_aware() -> None:
    client = ClickHouseClient(docker_context="orbstack")
    with pytest.raises(ValueError, match="timezone-aware"):
        list(
            client.traces(
                start=datetime(2026, 1, 1),
                end=datetime(2026, 1, 2),
                start_bucket=1,
                end_bucket=2,
            )
        )
    with pytest.raises(ValueError, match="ordered"):
        list(
            client.traces(
                start=datetime(2026, 1, 2, tzinfo=UTC),
                end=datetime(2026, 1, 1, tzinfo=UTC),
                start_bucket=1,
                end_bucket=2,
            )
        )


def test_selected_id_hydration_uses_parameters_for_every_identifier() -> None:
    predicate, parameters = query_selected_ids(("log-a", "log-b"))
    assert predicate == "id IN ({id_0:String}, {id_1:String})"
    assert parameters == {"id_0": "log-a", "id_1": "log-b"}
    with pytest.raises(ValueError):
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
            identity_kind="call_id",
            identifiers=("call-a",),
            start_ns=1,
            end_ns=2,
            start_bucket=1,
            end_bucket=2,
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
            identity_kind="log_id",
            identifiers=("log-a", "log-a", "log' OR 1=1"),
            start_ns=1,
            end_ns=2,
            start_bucket=1,
            end_bucket=2,
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
                identity_kind="trace_id",
                identifiers=("",),
                start_ns=1,
                end_ns=2,
                start_bucket=1,
                end_bucket=2,
            )
        )

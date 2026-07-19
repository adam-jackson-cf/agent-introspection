from __future__ import annotations

import subprocess
from datetime import UTC, datetime

import pytest

from agent_introspection.source import (
    LOG_QUERY,
    TRACE_QUERY,
    ClickHouseClient,
    SourceError,
    parse_duration_ms,
    parse_log_row,
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
    for raw_key in ("prompt", "arguments", "output", "error.message", "body"):
        assert f"['{raw_key}']" not in LOG_QUERY
    assert "{start:DateTime64(9)}" in TRACE_QUERY
    assert "{end:DateTime64(9)}" in TRACE_QUERY


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

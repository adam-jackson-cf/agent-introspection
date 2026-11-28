from __future__ import annotations

import base64
from collections.abc import Iterator

import pytest

from agent_introspection import facts
from agent_introspection.config import AppConfig, parse_config
from tests.support.clickhouse_http import ClickHouseHttp, Received

pytestmark = pytest.mark.boundary


@pytest.fixture
def clickhouse() -> Iterator[ClickHouseHttp]:
    server = ClickHouseHttp()
    yield server
    server.close()


def http_config(server: ClickHouseHttp) -> AppConfig:
    return parse_config({
        "signoz": {
            "clickhouse_url": server.url,
            "clickhouse_user": "facts",
            "clickhouse_password_env": "CH_PASSWORD",
        }
    })


def test_http_runner_posts_each_statement_to_a_real_http_server(
    clickhouse: ClickHouseHttp, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CH_PASSWORD", "s3cret")
    clickhouse.answers = [(200, b"1\n"), (200, b"2\n")]

    assert facts.http_runner(http_config(clickhouse))("SELECT 1;\nSELECT 2;") == "1\n2\n"

    token = base64.b64encode(b"facts:s3cret").decode()
    assert clickhouse.received == [
        Received("/?wait_end_of_query=1", f"Basic {token}", b"SELECT 1"),
        Received("/?wait_end_of_query=1", f"Basic {token}", b"SELECT 2"),
    ]


def test_http_runner_reads_the_exception_line_of_a_real_error_response(
    clickhouse: ClickHouseHttp, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CH_PASSWORD", "s3cret")
    clickhouse.answers = [(403, b"Code: 497. DB::Exception: facts: Not enough privileges\ntrace")]

    with pytest.raises(facts.FactsError, match="Code: 497") as raised:
        facts.http_runner(http_config(clickhouse))("SELECT 1")

    assert "s3cret" not in str(raised.value)


def test_http_runner_reports_a_server_that_is_gone(
    clickhouse: ClickHouseHttp, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CH_PASSWORD", "s3cret")
    config = http_config(clickhouse)
    clickhouse.close()

    with pytest.raises(facts.FactsError, match="ClickHouse HTTP request failed"):
        facts.http_runner(config)("SELECT 1")

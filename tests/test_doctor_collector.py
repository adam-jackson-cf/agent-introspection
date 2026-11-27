from __future__ import annotations

import socket
from dataclasses import replace
from pathlib import Path

import pytest

from agent_introspection import doctor
from tests.support.doctor_machine import Machine, build_machine, one
from tests.support.otlp_collector import Collector

pytestmark = pytest.mark.boundary


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Machine:
    return build_machine(tmp_path, monkeypatch)


def test_live_posts_one_empty_logs_request_to_the_hooks_endpoint(
    machine: Machine, collector: Collector
) -> None:
    machine.config.write_text(
        machine.config.read_text().replace("http://127.0.0.1:4318/v1/logs", collector.url)
    )
    port = int(collector.base.rsplit(":", 1)[1])
    probes = replace(
        machine.probes,
        tcp_open=lambda host, wanted: wanted != port or doctor.tcp_open(host, wanted),
        post_json=doctor.post_json,
    )

    result = machine.report(probes, live=True)

    assert one(result, "hooks_endpoint")["status"] == "pass"
    assert one(result, "collector_live")["status"] == "pass"
    assert collector.bodies == [{"resourceLogs": []}]
    collector.default = (404, "not found")
    assert one(machine.report(probes, live=True), "collector_live")["status"] == "fail"


def test_tcp_open_and_http_get_talk_to_a_local_server(collector: Collector) -> None:
    port = int(collector.base.rsplit(":", 1)[1])
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        closed = unused.getsockname()[1]

    assert doctor.tcp_open("127.0.0.1", port) is True
    assert doctor.tcp_open("127.0.0.1", closed) is False
    assert doctor.http_get(f"{collector.base}/ping") == (501, b"")

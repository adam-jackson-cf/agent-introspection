"""``doctor`` on an additional machine: no SigNoz here, everything sent to the host."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agent_introspection import doctor
from tests.support.doctor_machine import (
    CLAUDE_ENV,
    HOST,
    SPOOL_SYNC,
    Machine,
    build_machine,
    find,
    make_additional,
    one,
    replace_settings,
    settings_of,
    tree,
)

_LEFT_TO_THE_HOST = ("signoz", "schedule", "prompt_labels", "dashboard")
_NO_CLICKHOUSE = "an additional machine has no ClickHouse; run doctor on the host"
_HOST_LOGS = f"http://{HOST}:4318/v1/logs"
_HOOKS_FIX = (
    "remove [hooks] otlp_logs_endpoint from the config, so records go to "
    "[machine] export_endpoint's /v1/logs"
)


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Machine:
    built = build_machine(tmp_path, monkeypatch)
    make_additional(built)
    return built


def test_an_additional_machine_checks_the_host_collector_and_leaves_signoz_to_the_host(
    machine: Machine,
) -> None:
    dialled: list[tuple[str, int]] = []

    def tcp_open(host: str, port: int) -> bool:
        dialled.append((host, port))
        return True

    before = tree(machine.home.parent)

    result = machine.report(replace(machine.probes, tcp_open=tcp_open), drift=True)

    assert result["passed"] is True, [c for c in result["checks"] if c["status"] == "fail"]
    assert [item["name"] for item in result["checks"] if "harness" not in item] == [
        "config",
        "signoz",
        "host_collector",
        "hooks_endpoint",
        "disabled_harnesses",
        "spool",
        "schedule",
        "spool_sync",
        "schedule_linger",
        "prompt_labels",
        "dashboard",
        "drift",
    ]
    for name in _LEFT_TO_THE_HOST:
        item = one(result, name)
        assert (item["status"], item["fix"]) == (
            "info",
            "run `agent-introspection doctor` on the host",
        )
        assert item["detail"].startswith("not checked on an additional machine: ")
    assert one(result, "drift")["detail"] == f"not checked: {_NO_CLICKHOUSE}"
    assert {item["detail"] for item in result["checks"] if item["name"] == "telemetry"} == {
        f"not counted: {_NO_CLICKHOUSE}"
    }
    # Harness export is judged against the host's collector, not a local one.
    exports = [item for item in result["checks"] if item["name"] == "export_settings"]
    assert {item["status"] for item in exports} == {"pass"}
    claude = settings_of(one(result, "export_settings", "claude-code"))
    assert claude["OTEL_EXPORTER_OTLP_ENDPOINT"] == ("pass", "satisfied (grpc)")
    assert one(result, "host_collector")["detail"] == (
        f"the host's collector {HOST}:4318 accepts connections"
    )
    assert one(result, "hooks_endpoint")["detail"] == f"{HOST}:4318 accepts connections"
    assert {host for host, _ in dialled} == {HOST}
    assert tree(machine.home.parent) == before


def test_live_posts_the_empty_request_to_the_hosts_collector(machine: Machine) -> None:
    result = machine.report(live=True)

    assert one(result, "collector_live")["status"] == "pass"
    assert machine.posted == [(_HOST_LOGS, doctor.EMPTY_LOGS_REQUEST)]


def test_records_and_exports_that_miss_the_host_are_named(machine: Machine) -> None:
    machine.config.write_text(
        machine.config.read_text().replace(_HOST_LOGS, "http://127.0.0.1:4318/v1/logs")
    )
    replace_settings(machine, CLAUDE_ENV)
    probes = replace(machine.probes, tcp_open=lambda host, _port: host != HOST)

    result = machine.report(probes)

    assert result["passed"] is False
    collector = one(result, "host_collector")
    assert collector["status"] == "fail"
    assert "[machine] export_endpoint" in collector["fix"]
    hooks = one(result, "hooks_endpoint")
    assert hooks["status"] == "warn"
    assert hooks["detail"].startswith("127.0.0.1:4318 accepts connections but is not the host's")
    assert hooks["fix"] == _HOOKS_FIX
    claude = settings_of(one(result, "export_settings", "claude-code"))
    assert claude["OTEL_EXPORTER_OTLP_ENDPOINT"] == (
        "fail",
        f"is 'http://localhost:4317', expected 'http://{HOST}:4318'",
    )


def test_without_a_hooks_endpoint_records_go_to_the_hosts_logs_url(machine: Machine) -> None:
    machine.config.write_text(
        machine.config.read_text().replace(f'[hooks]\notlp_logs_endpoint = "{_HOST_LOGS}"\n', "")
    )

    result = machine.report(live=True)

    assert one(result, "hooks_endpoint")["status"] == "pass"
    assert machine.posted == [(_HOST_LOGS, doctor.EMPTY_LOGS_REQUEST)]


@pytest.mark.parametrize(
    ("status", "expected", "fix"),
    [
        (SPOOL_SYNC, {"spool_sync": "pass", "schedule_linger": "pass"}, ""),
        (
            {**SPOOL_SYNC, "linger": False},
            {"spool_sync": "pass", "schedule_linger": "warn"},
            "",
        ),
        (
            {**SPOOL_SYNC, "loaded": False},
            {"spool_sync": "fail", "schedule_linger": "pass"},
            "systemctl --user enable --now agent-introspection-spool-sync.timer",
        ),
        (
            {**SPOOL_SYNC, "installed": False, "loaded": False},
            {"spool_sync": "fail", "schedule_linger": "pass"},
            "install the onboarding skill's agent-introspection-spool-sync units",
        ),
        (
            {"scheduler": None, "platform": "darwin"},
            {"spool_sync": "info"},
            "run `agent-introspection spool sync` every minute",
        ),
    ],
)
def test_the_spool_sync_timer_stands_in_for_the_hosts_schedule(
    machine: Machine, status: dict[str, Any], expected: dict[str, str], fix: str
) -> None:
    result = machine.report(replace(machine.probes, spool_sync_status=lambda: status))

    found = {item["name"]: item for item in result["checks"]}
    assert {
        name: found[name]["status"] for name in ("spool_sync", "schedule_linger") if name in found
    } == expected
    assert found["spool_sync"]["fix"].startswith(fix)
    assert one(result, "schedule")["status"] == "info"
    assert find(result, "schedule_legacy") == []

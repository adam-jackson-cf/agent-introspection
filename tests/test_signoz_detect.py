from __future__ import annotations

import json

import pytest

from agent_introspection import signoz_detect
from agent_introspection.cli import main
from tests.support.signoz_machine import CLICKHOUSE, SIGNOZ_UI, UNRELATED, FakeMachine


@pytest.fixture
def machine(monkeypatch: pytest.MonkeyPatch) -> FakeMachine:
    return FakeMachine(monkeypatch)


def test_detect_reports_signoz_and_clickhouse_containers_and_busy_ports(
    machine: FakeMachine,
) -> None:
    machine([UNRELATED, SIGNOZ_UI, CLICKHOUSE], open_ports=(4317, 4318, 8080))

    result = signoz_detect.detect()

    assert result["docker"] == {"cli": True, "context": "orbstack", "reachable": True}
    assert [container["name"] for container in result["containers"]] == [
        "signoz",
        "signoz-telemetrystore-clickhouse-0-0",
    ]
    assert result["containers"][1]["ports"] == ["8123/tcp", "9000/tcp"]
    assert result["ports"] == {"4317": True, "4318": True, "8123": False, "8080": True}
    assert (result["clickhouse_ping"], result["signoz_present"]) == (False, True)
    notes = " ".join(result["notes"])
    assert "signoz-telemetrystore-clickhouse-0-0 does not answer on 127.0.0.1:8123" in notes
    assert "docker-compose.clickhouse-http.yaml" in notes
    assert "http://signoz-telemetrystore-clickhouse-0-0.orb.local:8123" in notes
    assert "never reconfigure" in notes


def test_a_clickhouse_answering_on_loopback_counts_as_signoz(machine: FakeMachine) -> None:
    machine(docker=None, open_ports=(8123,), ping=True)

    result = signoz_detect.detect()

    assert result["docker"] == {"cli": False, "context": None, "reachable": False}
    assert result["containers"] == []
    assert (result["clickhouse_ping"], result["signoz_present"]) == (True, True)
    notes = " ".join(result["notes"])
    assert "Docker CLI not found" in notes
    assert "the default [signoz] clickhouse_url" in notes


def test_clickhouse_is_pinged_only_when_its_port_is_open(machine: FakeMachine) -> None:
    machine(ping=True)

    assert signoz_detect.detect()["clickhouse_ping"] is False


@pytest.mark.parametrize("ps", [1, -1], ids=["daemon-down", "timeout"])
def test_an_unreachable_daemon_is_reported_not_raised(machine: FakeMachine, ps: int) -> None:
    machine([SIGNOZ_UI], ps=ps)

    result = signoz_detect.detect()

    assert result["docker"]["reachable"] is False
    assert result["containers"] == []
    assert result["signoz_present"] is False
    assert "did not answer `docker ps`" in " ".join(result["notes"])


@pytest.mark.parametrize(
    ("open_ports", "expected"),
    [((), "guided install (.agents/"), ((4318,), "Ports in use: 4318.")],
    ids=["free", "busy"],
)
def test_install_notes_follow_the_ports(
    machine: FakeMachine, open_ports: tuple[int, ...], expected: str
) -> None:
    machine(open_ports=open_ports)

    result = signoz_detect.detect()

    assert result["signoz_present"] is False
    assert expected in " ".join(result["notes"])


def test_detect_checks_a_signoz_on_other_ports(machine: FakeMachine) -> None:
    machine(open_ports=(14318,), ping=True)

    result = signoz_detect.detect("http://127.0.0.1:18123", "http://127.0.0.1:14318")

    assert result["clickhouse_url"] == "http://127.0.0.1:18123"
    assert (result["clickhouse_ping"], result["otlp_open"], result["signoz_present"]) == (
        True,
        True,
        True,
    )
    notes = " ".join(result["notes"])
    assert "--clickhouse-url" in notes
    assert "--otlp-endpoint" in notes
    assert "`ports` covers only SigNoz's default ports" in notes


def test_detect_reports_given_addresses_that_do_not_answer(machine: FakeMachine) -> None:
    machine()

    result = signoz_detect.detect("http://127.0.0.1:18123", "127.0.0.1:14318")

    assert (result["clickhouse_ping"], result["otlp_open"], result["signoz_present"]) == (
        False,
        False,
        False,
    )
    notes = " ".join(result["notes"])
    assert "does not answer /ping at http://127.0.0.1:18123" in notes
    assert "Nothing accepts connections at 127.0.0.1:14318" in notes
    assert "rerun with --clickhouse-url" not in notes


def test_detect_without_addresses_points_at_the_flags(machine: FakeMachine) -> None:
    machine()

    result = signoz_detect.detect()

    assert (result["otlp_endpoint"], result["otlp_open"]) == (None, None)
    assert "rerun with --clickhouse-url and --otlp-endpoint" in " ".join(result["notes"])


def test_signoz_detect_command_passes_the_addresses(
    machine: FakeMachine, capsys: pytest.CaptureFixture[str]
) -> None:
    machine(open_ports=(14318,), ping=True)

    argv = ["signoz", "detect", "--clickhouse-url", "http://127.0.0.1:18123"]
    assert main([*argv, "--otlp-endpoint", "http://127.0.0.1:14318"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert (result["clickhouse_ping"], result["otlp_open"]) == (True, True)


def test_signoz_detect_command_prints_one_json_object(
    machine: FakeMachine, capsys: pytest.CaptureFixture[str]
) -> None:
    machine([CLICKHOUSE])

    assert main(["signoz", "detect"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["signoz_present"] is True
    assert set(result) >= {"docker", "containers", "ports", "signoz_present", "notes"}


def test_detect_says_when_setup_would_refuse_the_addresses(machine: FakeMachine) -> None:
    machine(open_ports=(4317,), ping=True)

    result = signoz_detect.detect("http://10.0.0.5:8123", "http://127.0.0.1:4317")

    assert "must point at this machine" in result["setup_refusal"]
    assert "`setup` would refuse these addresses" in " ".join(result["notes"])


def test_endpoint_open_defaults_https_to_443(monkeypatch: pytest.MonkeyPatch) -> None:
    dialled: list[tuple[str, int]] = []

    def port_open(port: int, host: str = "") -> bool:
        dialled.append((host, port))
        return True

    monkeypatch.setattr(signoz_detect, "port_open", port_open)

    signoz_detect.endpoint_open("https://collector.local")

    assert dialled == [("collector.local", 443)]

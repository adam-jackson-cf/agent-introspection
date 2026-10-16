from __future__ import annotations

import json
import socket
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agent_introspection import cli_doctor, config, doctor, facts, producers, signoz_detect
from agent_introspection.cli import EXIT_FACTS, main
from tests.support.doctor_machine import (
    CLAUDE_ENV,
    ENABLED,
    SECRET,
    SERVICES,
    Machine,
    build_machine,
    find,
    one,
    replace_settings,
    settings_of,
    tree,
    write,
)
from tests.support.otlp_collector import Collector


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Machine:
    return build_machine(tmp_path, monkeypatch)


def test_a_healthy_machine_passes_and_the_doctor_only_reads(machine: Machine) -> None:
    before = tree(machine.home.parent)

    result = machine.report()

    assert result["passed"] is True, [c for c in result["checks"] if c["status"] == "fail"]
    assert result["summary"]["fail"] == 0
    assert sum(result["summary"].values()) == len(result["checks"])
    assert {item["status"] for item in result["checks"]} <= {"pass", "off"}
    assert tree(machine.home.parent) == before
    assert machine.posted == []
    assert all(sql.split()[0] in ("SELECT", "SHOW", "CHECK") for sql in machine.executed)
    telemetry = [sql for sql in machine.executed if "rows_24h" in sql]
    assert len(telemetry) == 1
    assert all(f"'{service}'" in telemetry[0] for service in SERVICES)
    assert [item.get("harness") for item in find(result, "telemetry", "codex")] == ["codex"]


def test_secret_values_never_appear_in_the_report(
    machine: Machine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    inbox: Path,
) -> None:
    endpoint = f"https://user:{SECRET}@collector.example:4318/?t={SECRET}"
    replace_settings(machine, {**CLAUDE_ENV, "OTEL_EXPORTER_OTLP_ENDPOINT": endpoint})
    (machine.home / ".codex/config.toml").write_text(
        "[otel]\nexporter = { otlp-http = { endpoint = "
        f'"https://collector.example/v1/logs", protocol = "binary", headers = {{ k = "{SECRET}" }} '
        "} }\n"
    )
    probes = replace(
        machine.probes,
        harness=replace(machine.probes.harness, environ={"OPENROUTER_API_KEY": SECRET}),
    )
    monkeypatch.setattr(cli_doctor, "default_probes", lambda: probes)

    code = main(["--config", str(machine.config), "doctor", "--live", "--drift"])

    output = capsys.readouterr().out
    assert code == EXIT_FACTS
    assert SECRET not in output
    claude = settings_of(one(json.loads(output), "export_settings", "claude-code"))
    assert claude["OTEL_EXPORTER_OTLP_ENDPOINT"] == (
        "fail",
        "is 'https://collector.example:4318/', expected 'http://127.0.0.1:4318'",
    )
    codex = settings_of(find(json.loads(output), "export_settings", "codex")[0])
    assert codex["exporter"][1].startswith("is otlp-http https://collector.example/v1/logs,")


def test_a_check_that_raises_reports_only_its_error_class(machine: Machine) -> None:
    def broken() -> dict[str, Any]:
        raise RuntimeError(f"launchctl said {SECRET}")

    def no_which(_binary: str) -> str | None:
        raise PermissionError(SECRET)

    probes = replace(
        machine.probes,
        schedule_status=broken,
        harness=replace(machine.probes.harness, which=no_which),
    )

    result = machine.report(probes)

    schedule = one(result, "schedule")
    assert (schedule["status"], schedule["detail"]) == (
        "fail",
        "the check raised RuntimeError",
    )
    assert one(result, "binary", "omp")["detail"] == "the check raised PermissionError"
    assert SECRET not in json.dumps(result)
    assert one(result, "dashboard")["status"] == "pass"
    assert result["summary"]["fail"] == 4


def _unreachable(_config: config.AppConfig) -> facts.SqlRunner:
    def run(_sql: str) -> str:
        raise facts.FactsError("Error response from daemon: No such container")

    return run


@pytest.mark.parametrize(
    ("present", "fix"),
    [
        (False, "SigNoz seems absent: only if the user asks for a new one, run the onboarding"),
        (True, "SigNoz is present but its ClickHouse HTTP interface does not answer"),
    ],
)
def test_an_unreachable_clickhouse_asks_signoz_detect_what_to_fix(
    machine: Machine, present: bool, fix: str
) -> None:
    probes = replace(
        machine.probes,
        connect=_unreachable,
        detect=lambda: {"signoz_present": present},
        schedule_status=lambda: {"scheduler": "launchd", "label": "x", "installed": False},
    )

    result = machine.report(probes, drift=True)

    signoz = one(result, "signoz")
    assert signoz["status"] == "fail"
    assert signoz["detail"] == (
        "ClickHouse is unreachable: Error response from daemon: No such container"
    )
    assert signoz["fix"].startswith(fix)
    assert one(result, "telemetry", "omp")["status"] == "info"
    assert one(result, "schedule")["status"] == "info"
    assert one(result, "drift")["status"] == "info"


def test_a_failing_signoz_detect_still_gives_a_fix(machine: Machine) -> None:
    def broken() -> dict[str, Any]:
        raise OSError("docker")

    probes = replace(machine.probes, connect=_unreachable, detect=broken)

    signoz = one(machine.report(probes), "signoz")

    assert signoz["fix"].startswith("`signoz detect` raised OSError")


def test_failing_preflight_checks_are_named(machine: Machine) -> None:
    probes = replace(machine.probes, connect=lambda _config: machine.run_sql({"INSERT": "0"}))

    result = machine.report(probes)

    signoz = one(result, "signoz")
    assert signoz["status"] == "fail"
    assert signoz["detail"] == "failing facts preflight checks: facts_database_grants"
    assert signoz["failing"] == ["facts_database_grants"]
    assert "facts preflight" in signoz["fix"]
    assert one(result, "telemetry", "omp")["status"] == "pass"


def test_closed_collector_ports_and_a_missing_hooks_endpoint(machine: Machine) -> None:
    machine.config.write_text(
        machine.config.read_text().replace(
            '[hooks]\notlp_logs_endpoint = "http://127.0.0.1:4318/v1/logs"\n', ""
        )
    )
    probes = replace(machine.probes, tcp_open=lambda _host, _port: False)

    result = machine.report(probes)

    assert one(result, "collector_http")["status"] == "fail"
    assert one(result, "collector_grpc")["status"] == "warn"
    hooks_endpoint = one(result, "hooks_endpoint")
    assert hooks_endpoint["status"] == "fail"
    assert "otlp_logs_endpoint" in hooks_endpoint["fix"]


def test_the_collector_checks_dial_the_configured_otlp_http_port(machine: Machine) -> None:
    machine.config.write_text(
        machine.config.read_text() + '[signoz]\notlp_endpoint = "http://127.0.0.1:14318"\n'
    )
    dialled: list[tuple[str, int]] = []

    def tcp_open(host: str, port: int) -> bool:
        dialled.append((host, port))
        return True

    result = machine.report(replace(machine.probes, tcp_open=tcp_open))

    assert {("127.0.0.1", 14318), ("127.0.0.1", 4317)} <= set(dialled)
    details = {name: one(result, name)["detail"] for name in ("collector_http", "collector_grpc")}
    assert details == {
        "collector_http": "OTLP/HTTP 127.0.0.1:14318 accepts connections",
        "collector_grpc": "OTLP/gRPC 127.0.0.1:4317 accepts connections",
    }


def test_a_partly_enabled_profile_counts_only_its_enabled_surfaces(machine: Machine) -> None:
    machine.config.write_text(machine.config.read_text().replace('"codex"]', '"codex_exec"]'))

    result = machine.report()

    codex = one(result, "telemetry", "codex")
    assert codex["status"] == "pass"
    assert set(codex["services"]) == {"codex_exec"}
    [telemetry] = [sql for sql in machine.executed if "rows_24h" in sql]
    assert "'codex_exec'" in telemetry
    assert "'codex-app-server'" not in telemetry
    assert "'codex_cli_rs'" not in telemetry


def test_without_a_hooks_endpoint_records_go_to_the_collectors_logs_url(
    machine: Machine,
) -> None:
    machine.config.write_text(
        machine.config.read_text().replace(
            '[hooks]\notlp_logs_endpoint = "http://127.0.0.1:4318/v1/logs"\n', ""
        )
    )
    environ = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://elsewhere.example:4318"}
    probes = replace(machine.probes, harness=replace(machine.probes.harness, environ=environ))

    hooks_endpoint = one(machine.report(probes), "hooks_endpoint")

    assert (hooks_endpoint["status"], hooks_endpoint["detail"]) == (
        "pass",
        "127.0.0.1:4318 accepts connections",
    )
    unreachable = replace(probes, tcp_open=lambda _host, port: port != 4318)
    assert one(machine.report(unreachable), "hooks_endpoint")["status"] == "fail"


def test_live_posts_one_empty_logs_request_to_the_hooks_endpoint(
    machine: Machine, collector: Collector
) -> None:
    machine.config.write_text(
        machine.config.read_text().replace("http://127.0.0.1:4318/v1/logs", collector.url)
    )
    port = int(collector.base.rsplit(":", 1)[1])
    probes = replace(
        machine.probes,
        # Only the test collector is really dialled; the standard OTLP ports are faked.
        tcp_open=lambda host, wanted: wanted != port or doctor.tcp_open(host, wanted),
        post_json=doctor.post_json,
    )

    result = machine.report(probes, live=True)

    assert one(result, "hooks_endpoint")["status"] == "pass"
    assert one(result, "collector_live")["status"] == "pass"
    assert collector.bodies == [{"resourceLogs": []}]
    collector.default = (404, "not found")
    assert one(machine.report(probes, live=True), "collector_live")["status"] == "fail"


def test_live_reports_a_collector_that_does_not_answer(machine: Machine) -> None:
    def refused(_url: str, _body: bytes) -> int:
        raise ConnectionRefusedError

    result = machine.report(replace(machine.probes, post_json=refused), live=True)

    live = one(result, "collector_live")
    assert (live["status"], live["detail"]) == (
        "fail",
        "an empty OTLP/HTTP logs request got no answer (ConnectionRefusedError)",
    )


def test_tcp_open_and_http_get_talk_to_a_local_server(collector: Collector) -> None:
    port = int(collector.base.rsplit(":", 1)[1])
    # A port taken and released: nothing listens, so a connection is refused at once on
    # every platform, never left to the connect timeout.
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        closed = unused.getsockname()[1]

    assert doctor.tcp_open("127.0.0.1", port) is True
    assert doctor.tcp_open("127.0.0.1", closed) is False
    assert doctor.http_get(f"{collector.base}/ping") == (501, b"")


def test_an_unknown_enabled_harness_fails_the_harness_block(machine: Machine) -> None:
    machine.config.write_text(machine.config.read_text().replace('"codex"]', '"cursor"]'))

    result = machine.report()

    harnesses = one(result, "harnesses")
    assert harnesses["status"] == "fail"
    assert "unknown harness cursor" in harnesses["detail"]
    assert find(result, "binary", "omp") == []


def test_the_spool_warns_on_rejections_and_a_misconfigured_endpoint(machine: Machine) -> None:
    write(machine.inbox / "rejected/a.json", "{}")
    write(machine.inbox / "b.json", "{}")
    write(
        machine.inbox.parent / "spool-state.json",
        json.dumps({"sent_total": {}, "last_error_class": "endpoint_misconfigured"}),
    )

    spool = one(machine.report(), "spool")

    assert spool["status"] == "warn"
    assert spool["detail"] == "1 pending, 1 rejected; last send error endpoint_misconfigured"
    assert "move them back" in spool["fix"]
    assert "/v1/logs" in spool["fix"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (
            {"scheduler": "launchd", "label": "dev", "installed": True, "loaded": False},
            {"schedule": "fail"},
        ),
        (
            {"scheduler": "launchd", "label": "dev", "installed": False, "loaded": False},
            {"schedule": "fail"},
        ),
        (
            {
                "scheduler": "launchd",
                "label": "dev",
                "installed": False,
                "loaded": False,
                "legacy": {"label": "com.old", "installed": True, "loaded": True},
            },
            {"schedule": "warn"},
        ),
        (
            {
                "scheduler": "launchd",
                "label": "dev",
                "installed": True,
                "loaded": True,
                "legacy": {"label": "com.old", "installed": True, "loaded": True},
            },
            {"schedule": "pass", "schedule_legacy": "warn"},
        ),
        (
            {
                "scheduler": "launchd",
                "label": "dev",
                "installed": False,
                "loaded": False,
                "legacy": {"label": "com.old", "installed": True, "loaded": False},
            },
            {"schedule": "fail", "schedule_legacy": "warn"},
        ),
        (
            {
                "scheduler": "systemd",
                "unit": "t",
                "installed": True,
                "loaded": True,
                "linger": False,
            },
            {"schedule": "pass", "schedule_linger": "warn"},
        ),
        (
            {
                "scheduler": "systemd",
                "unit": "t",
                "installed": True,
                "loaded": True,
                "linger": True,
            },
            {"schedule": "pass", "schedule_linger": "pass"},
        ),
    ],
)
def test_the_schedule_on_the_signoz_host(
    machine: Machine, status: dict[str, Any], expected: dict[str, str]
) -> None:
    result = machine.report(replace(machine.probes, schedule_status=lambda: status))

    names = {item["name"] for item in result["checks"] if item["name"].startswith("schedule")}
    assert names == set(expected)
    assert {name: one(result, name)["status"] for name in expected} == expected


@pytest.mark.parametrize(
    ("command", "environ", "omp", "expected"),
    [
        (True, {}, None, "pass"),
        (False, {"OPENROUTER_API_KEY": SECRET}, None, "info"),
        (False, {}, "/bin/omp", "info"),
        (False, {}, None, "off"),
    ],
)
def test_prompt_labels_check_presence_only(
    machine: Machine, command: bool, environ: dict[str, str], omp: str | None, expected: str
) -> None:
    if not command:
        machine.config.write_text(
            machine.config.read_text().replace(
                '[labels]\nopenrouter_key_command = ["security", "find-generic-password", "-w"]\n',
                "",
            )
        )
    harness = replace(
        machine.probes.harness,
        environ=environ,
        which=lambda binary: omp if binary == "omp" else f"/bin/{binary}",
    )

    labels = one(machine.report(replace(machine.probes, harness=harness)), "prompt_labels")

    assert labels["status"] == expected
    assert SECRET not in json.dumps(labels)


@pytest.mark.parametrize(
    ("environ", "answer", "expected", "detail"),
    [
        ({}, (200, b"Ok.\n"), "pass", "ClickHouse at [signoz] clickhouse_url answers /ping"),
        (
            {},
            (404, b""),
            "warn",
            "ClickHouse at [signoz] clickhouse_url answered /ping with HTTP 404",
        ),
        (
            {"INTROSPECTION_CLICKHOUSE_URL": "http://clickhouse.example:8123"},
            (200, b"Ok.\n"),
            "warn",
            "the URL in INTROSPECTION_CLICKHOUSE_URL is not on this machine, so the dashboard "
            "refuses it",
        ),
    ],
)
def test_the_dashboard_url_must_be_local_and_answer_ping(
    machine: Machine,
    environ: dict[str, str],
    answer: tuple[int, bytes],
    expected: str,
    detail: str,
) -> None:
    probes = replace(
        machine.probes,
        http_get=lambda _url: answer,
        harness=replace(machine.probes.harness, environ=environ),
    )

    dashboard = one(machine.report(probes), "dashboard")

    assert (dashboard["status"], dashboard["detail"]) == (expected, detail)
    assert "clickhouse.example" not in json.dumps(dashboard)


def test_an_unanswered_dashboard_ping_warns_with_the_compose_note(machine: Machine) -> None:
    def timed_out(_url: str) -> tuple[int, bytes]:
        raise TimeoutError

    dashboard = one(machine.report(replace(machine.probes, http_get=timed_out)), "dashboard")

    assert dashboard["status"] == "warn"
    assert dashboard["detail"].endswith("does not answer /ping (TimeoutError)")
    assert "does not publish port 8123" in dashboard["fix"]


def test_drift_warns_per_drifted_or_unreadable_service_and_unregistered_or_unnamed_producer(
    machine: Machine,
) -> None:
    calls: list[tuple[Path | None, Sequence[str]]] = []

    def drift(_run: facts.SqlRunner, saved: Path | None, services: Sequence[str]) -> dict[str, Any]:
        calls.append((saved, services))
        return {
            "services": [
                {
                    "service": "claude-code",
                    "status": "drifted",
                    "added": {"span_names": ["a", "b"], "event_names": []},
                    "removed": {"attribute_keys": ["c"]},
                },
                {"service": "oh-my-pi", "status": "unchanged"},
                {"service": "codex_exec", "status": "invalid_baseline"},
                {"service": "codex_cli_rs", "status": "no_baseline"},
            ],
            "unregistered": ["opencode"],
            "unnamed_rows": 12,
            "drifted": True,
        }

    result = machine.report(replace(machine.probes, drift_report=drift), drift=True)

    drift_checks = {item["service"]: item for item in find(result, "drift")}
    assert {service: item["status"] for service, item in drift_checks.items()} == {
        "claude-code": "warn",
        "oh-my-pi": "pass",
        "codex_exec": "warn",
        "codex_cli_rs": "info",
        "opencode": "warn",
    }
    assert drift_checks["claude-code"]["detail"].startswith("claude-code gained 2 and lost 1")
    assert "producers profile opencode" in drift_checks["opencode"]["fix"]
    # Rows no service name claims are counted only; the sender needs OTEL_SERVICE_NAME.
    unnamed = one(result, "drift_unnamed")
    assert (unnamed["status"], unnamed["unnamed_rows"]) == ("info", 12)
    assert unnamed["detail"].startswith("12 rows have an empty service.name")
    assert "set OTEL_SERVICE_NAME on the producer" in unnamed["fix"]
    assert calls == [(None, facts.enabled_harnesses(facts.load_registry(), ENABLED))]


def test_drift_without_any_baseline_is_one_info(machine: Machine) -> None:
    probes = replace(
        machine.probes,
        drift_report=lambda _run, _saved, services: {
            "services": [{"service": service, "status": "no_baseline"} for service in services],
            "unregistered": [],
            "unnamed_rows": 0,
            "drifted": False,
        },
    )

    result = machine.report(probes, drift=True)

    drift = one(result, "drift")
    assert (drift["status"], drift["detail"]) == ("info", "no baseline: no fingerprint is saved")
    assert "producers fingerprint --save" in drift["fix"]
    assert find(result, "drift_unnamed") == []
    assert find(machine.report(), "drift") == []


def test_a_config_that_does_not_load_fails_and_the_defaults_carry_on(
    machine: Machine,
) -> None:
    missing = machine.config.with_name("missing.toml")

    result = doctor.report(doctor.Options(config_path=missing, inbox=machine.inbox), machine.probes)

    config_check = one(result, "config")
    assert config_check["status"] == "fail"
    assert "configuration file does not exist" in config_check["detail"]
    assert find(result, "binary", "codex")


def test_an_absent_default_config_means_the_defaults(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    default = machine.config.with_name("default.toml")
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", default)
    monkeypatch.setattr(doctor, "DEFAULT_CONFIG_PATH", default)

    result = doctor.report(doctor.Options(inbox=machine.inbox), machine.probes)

    assert one(result, "config")["status"] == "info"
    # With no config, records go to the local collector's logs URL.
    assert one(result, "hooks_endpoint")["detail"] == "127.0.0.1:4318 accepts connections"


def test_the_command_prints_one_json_report_and_exits_by_its_result(
    machine: Machine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    inbox: Path,
) -> None:
    monkeypatch.setattr(cli_doctor, "default_probes", lambda: machine.probes)

    assert main(["--config", str(machine.config), "doctor"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["passed"] is True
    assert set(report["summary"]) == {"pass", "warn", "fail", "off", "info"}

    failing = replace(machine.probes, tcp_open=lambda _host, _port: False)
    monkeypatch.setattr(cli_doctor, "default_probes", lambda: failing)
    assert main(["--config", str(machine.config), "doctor"]) == EXIT_FACTS
    assert json.loads(capsys.readouterr().out)["passed"] is False


def test_the_command_wires_the_probes_that_touch_this_machine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(signoz_detect, "detect", lambda: {"signoz_present": False})
    seen: list[Path] = []

    def report(_run: facts.SqlRunner, saved: Path, _services: Sequence[str]) -> dict[str, Any]:
        seen.append(saved)
        return {"services": [], "unregistered": [], "drifted": False}

    monkeypatch.setattr(producers, "drift_report", report)
    probes = cli_doctor.default_probes()

    assert probes.connect is facts.runner
    assert probes.tcp_open is doctor.tcp_open
    assert probes.harness.home == Path.home()
    assert probes.detect() == {"signoz_present": False}
    probes.drift_report(lambda _sql: "", None, ())
    probes.drift_report(lambda _sql: "", tmp_path, ())
    assert seen == [producers.FINGERPRINT_DIR, tmp_path]

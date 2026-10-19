"""A fake machine for the doctor tests: a home with every harness set up, and probes.

``build_machine`` writes Claude Code, Codex (plus an Orca account home), and omp
config files, the managed activity shim and omp plugin link, and one session per
store, under a temporary home. Its probes never leave the test: ClickHouse answers
from ``fake_clickhouse``, every port is open, and each binary reports its verified
version. ``make_additional`` turns it into an additional machine that sends to a host.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest

from agent_introspection import doctor, doctor_harness, facts, hook_install, profiles
from tests.test_preflight import fake_clickhouse

SECRET = "sk-sentinel-SECRET-7f3a"


ENABLED = ("claude-code", "oh-my-pi", "codex")


SERVICES = ("claude-code", "oh-my-pi", "codex-app-server", "codex_cli_rs", "codex_exec")


CLAUDE_ENV = {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "1",
    "OTEL_METRICS_EXPORTER": "otlp",
    "OTEL_LOGS_EXPORTER": "otlp",
    "OTEL_TRACES_EXPORTER": "otlp",
    # gRPC on 4317: satisfies the profile's OTLP/HTTP endpoint and protocol.
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
    "OTEL_SERVICE_NAME": "claude-code",
    "ANTHROPIC_API_KEY": SECRET,
}


# Both Codex homes export prompts, as `harness configure codex --prompt-export` leaves
# them: a healthy machine has no home that exports while another does not.
CODEX_GRPC = """[otel]
log_user_prompt = true
exporter = { otlp-grpc = { endpoint = "http://localhost:4317" } }
trace_exporter = { otlp-grpc = { endpoint = "http://localhost:4317" } }
metrics_exporter = { otlp-grpc = { endpoint = "http://localhost:4317" } }
"""


CODEX_HTTP = "[otel]\nlog_user_prompt = true\n" + "".join(
    f'{key} = {{ otlp-http = {{ endpoint = "http://127.0.0.1:4318/v1/{signal}", '
    'protocol = "binary" } }\n'
    for key, signal in (
        ("exporter", "logs"),
        ("trace_exporter", "traces"),
        ("metrics_exporter", "metrics"),
    )
)


OMP_ENV = (
    "OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318\n"
    "OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf\n"
    "OTEL_SERVICE_NAME=oh-my-pi\n"
    f"OTEL_EXPORTER_OTLP_HEADERS=authorization={SECRET}\n"
    "AGENT_INTROSPECTION_OMP_PROMPT_EXPORT=1\n"
)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def verified_versions() -> dict[str, str]:
    return {profile.binary: profile.verified_versions[0] for profile in profiles.load_profiles()}


def telemetry_rows(counts: Mapping[str, tuple[int, int]], last_seen: int) -> str:
    rows = [
        {"source": source, "service": service, "rows_24h": str(rows), "last_seen": str(last_seen)}
        for service, pair in counts.items()
        for source, rows in zip(("spans", "logs"), pair, strict=True)
    ]
    return "\n".join(map(json.dumps, rows))


@dataclass
class Machine:
    """A fake home with every harness set up, and probes that never leave the test."""

    home: Path
    config: Path
    inbox: Path
    orca: Path
    executed: list[str] = field(default_factory=list)
    posted: list[tuple[str, bytes]] = field(default_factory=list)
    telemetry: dict[str, tuple[int, int]] = field(
        default_factory=lambda: dict.fromkeys(SERVICES, (5, 3))
    )
    probes: doctor.Probes = field(init=False)

    def run_sql(self, grants: Mapping[str, str] | None = None) -> facts.SqlRunner:
        """Answer preflight from ``fake_clickhouse`` (every grant held unless ``grants``
        says ``0``) and the telemetry query from ``telemetry``."""
        executed, answer = fake_clickhouse(grants=dict(grants or {}))
        self.executed = executed

        def run(sql: str) -> str:
            if "rows_24h" in sql:
                executed.append(sql)
                return telemetry_rows(self.telemetry, int(time.time()) - 60)
            preflight_answer: str = answer(sql)
            return preflight_answer

        return run

    def report(self, probes: doctor.Probes | None = None, **options: Any) -> dict[str, Any]:
        return doctor.report(
            doctor.Options(config_path=self.config, inbox=self.inbox, **options),
            probes or self.probes,
        )


def fake_probes(machine: Machine) -> doctor.Probes:
    versions = verified_versions()

    def post(url: str, body: bytes) -> int:
        machine.posted.append((url, body))
        return 200

    return doctor.Probes(
        connect=lambda _config: machine.run_sql(),
        tcp_open=lambda _host, _port: True,
        post_json=post,
        http_get=lambda _url: (200, b"Ok.\n"),
        schedule_status=lambda: {
            "scheduler": "launchd",
            "label": "dev.agent-introspection.sync",
            "installed": True,
            "loaded": True,
            "legacy": None,
        },
        spool_sync_status=lambda: pytest.fail("only an additional machine has a spool-sync timer"),
        dashboard_status=lambda: {"scheduler": "launchd", "installed": True, "loaded": True},
        detect=lambda: pytest.fail("signoz detect runs only when ClickHouse is unreachable"),
        drift_report=lambda _run, _saved, services: {
            "services": [{"service": service, "status": "unchanged"} for service in services],
            "unregistered": [],
            "drifted": False,
        },
        harness=doctor_harness.HarnessProbes(
            which=lambda binary: f"/usr/local/bin/{binary}",
            version=lambda argv: versions[Path(argv[0]).name],
            environ={},
            home=machine.home,
        ),
    )


def build_machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Machine:
    """Build a healthy fake home under ``tmp_path``, every enabled harness set up."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    for name in (
        "CODEX_HOME",
        "CLAUDE_CONFIG_DIR",
        "OPENROUTER_API_KEY",
        "INTROSPECTION_CLICKHOUSE_URL",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_PROTOCOL",
    ):
        monkeypatch.delenv(name, raising=False)
    runtime = home / doctor_harness.MANAGED_RUNTIME
    shim = write(runtime / doctor_harness.SHIM, hook_install.packaged(doctor_harness.SHIM))
    shim.chmod(0o755)
    for name in doctor_harness.OMP_PLUGIN_FILES:
        write(runtime / "omp" / name, hook_install.packaged(f"omp/{name}"))
    link = home / doctor_harness.OMP_PLUGIN_LINK
    link.parent.mkdir(parents=True)
    link.symlink_to(runtime / "omp")
    settings = hook_install.with_claude_hooks({"env": dict(CLAUDE_ENV)}, shim)
    write(home / ".claude/settings.json", json.dumps(settings, indent=2))
    write(home / ".omp/.env", OMP_ENV)
    write(home / ".codex/config.toml", CODEX_GRPC)
    # Orca keeps per-account Codex homes under a path with a space, as on macOS; the
    # codex profile's home_globs finds them.
    orca = home / "Library/Application Support/orca/codex-accounts/account-1/home"
    write(orca / "config.toml", CODEX_HTTP)
    config_path = write(
        tmp_path / "config.toml",
        f'[database]\npath = "{tmp_path / "workflow.sqlite3"}"\n'
        f"[harnesses]\nenabled = {json.dumps(list(ENABLED))}\n"
        '[hooks]\notlp_logs_endpoint = "http://127.0.0.1:4318/v1/logs"\n'
        '[labels]\nopenrouter_key_command = ["security", "find-generic-password", "-w"]\n',
    )
    # The hooks read this config, as `harness configure --config` points them.
    write(runtime / doctor_harness.CONFIG_POINTER, f"{config_path.resolve()}\n")
    built = Machine(home=home, config=config_path, inbox=tmp_path / "data/hook-inbox", orca=orca)
    built.probes = fake_probes(built)
    return built


def find(result: Mapping[str, Any], name: str, harness: str = "") -> list[dict[str, Any]]:
    return [
        item
        for item in result["checks"]
        if item["name"] == name and item.get("harness", "") == harness
    ]


def one(result: Mapping[str, Any], name: str, harness: str = "") -> dict[str, Any]:
    found = find(result, name, harness)
    assert len(found) == 1, found
    return found[0]


def settings_of(item: Mapping[str, Any]) -> dict[str, tuple[str, str]]:
    return {row["key"]: (row["status"], row["detail"]) for row in item["settings"]}


def tree(root: Path) -> dict[str, float]:
    return {str(path): path.lstat().st_mtime for path in root.rglob("*")}


def replace_settings(machine: Machine, env: Mapping[str, str]) -> None:
    path = machine.home / ".claude/settings.json"
    settings = json.loads(path.read_text())
    settings["env"] = dict(env)
    path.write_text(json.dumps(settings))


# The SigNoz host an additional machine sends to, and its healthy spool-sync timer.
HOST = "signoz-host.example"
SPOOL_SYNC = {
    "scheduler": "systemd",
    "unit": "agent-introspection-spool-sync.timer",
    "installed": True,
    "enabled": True,
    "loaded": True,
    "linger": True,
}


def make_additional(machine: Machine) -> None:
    """Turn ``machine`` into an additional machine whose harnesses and hooks send to ``HOST``.

    Its probes refuse ClickHouse, the host's scheduler, and the dashboard's ping, which an
    additional machine never touches.
    """
    hooks = "http://127.0.0.1:4318/v1/logs"
    machine.config.write_text(
        machine.config.read_text().replace(hooks, f"http://{HOST}:4318/v1/logs")
        + f'[machine]\nrole = "additional"\nexport_endpoint = "http://{HOST}:4318"\n'
    )
    for path in (
        machine.home / ".claude/settings.json",
        machine.home / ".omp/.env",
        machine.home / ".codex/config.toml",
        machine.orca / "config.toml",
    ):
        path.write_text(path.read_text().replace("localhost", HOST).replace("127.0.0.1", HOST))

    def host_only(*_args: object) -> Any:
        pytest.fail("an additional machine reached a host-only check")

    machine.probes = replace(
        machine.probes,
        connect=host_only,
        schedule_status=host_only,
        http_get=host_only,
        drift_report=host_only,
        spool_sync_status=lambda: dict(SPOOL_SYNC),
        dashboard_status=host_only,
    )

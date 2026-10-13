from __future__ import annotations

import json
import shutil
import socket
import stat
import subprocess
import sys
import threading
from collections.abc import Iterator, Sequence
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from agent_introspection import signoz_detect
from agent_introspection.cli import main

ROOT = Path(__file__).resolve().parents[1]
SIGNOZ_SCRIPTS = ROOT / ".agents/skills/introspection-onboarding/scripts/signoz"
INSTALL = SIGNOZ_SCRIPTS / "install-signoz.sh"

UNRELATED = {"Names": "paperclip-1", "Image": "ghcr.io/paperclip:1", "Ports": "3100/tcp"}
SIGNOZ_UI = {
    "Names": "signoz",
    "Image": "signoz/signoz:v0.145.0",
    "Ports": "0.0.0.0:8080->8080/tcp",
}
CLICKHOUSE = {
    "Names": "signoz-telemetrystore-clickhouse-0-0",
    "Image": "clickhouse/clickhouse-server:25.12.5",
    "Ports": "8123/tcp, 9000/tcp",
}


def _docker(rows: Sequence[dict[str, str]] = (), *, ps: int = 0, context: str = "orbstack") -> Any:
    """A `docker` seam answering `context show` and `docker ps`; ps=-1 times out."""

    def run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        if command[1:] == ("context", "show"):
            return subprocess.CompletedProcess(command, 0, f"{context}\n", "")
        assert command[1:] == ("ps", "--format", "{{json .}}")
        if ps < 0:
            raise subprocess.TimeoutExpired(command, 10)
        lines = [json.dumps(row) for row in rows] + ["not json", "[1]"]
        return subprocess.CompletedProcess(command, ps, "\n".join(lines) + "\n", "")

    return run


@pytest.fixture
def machine(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Fake this machine: Docker on PATH, every port free, ClickHouse silent."""

    def configure(
        rows: Sequence[dict[str, str]] = (),
        *,
        docker: str | None = "/usr/local/bin/docker",
        ps: int = 0,
        open_ports: Sequence[int] = (),
        ping: bool = False,
    ) -> None:
        monkeypatch.setattr(signoz_detect, "_docker_cli", lambda: docker)
        monkeypatch.setattr(signoz_detect, "_run_docker", _docker(rows, ps=ps))
        monkeypatch.setattr(signoz_detect, "port_open", lambda port: port in open_ports)
        monkeypatch.setattr(signoz_detect, "clickhouse_answers", lambda: ping)

    return configure


def test_detect_reports_signoz_and_clickhouse_containers_and_busy_ports(machine: Any) -> None:
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
    assert 'clickhouse_container = "signoz-telemetrystore-clickhouse-0-0"' in notes
    assert "never reconfigure" in notes


def test_a_clickhouse_answering_on_loopback_counts_as_signoz(machine: Any) -> None:
    machine(docker=None, open_ports=(8123,), ping=True)

    result = signoz_detect.detect()

    assert result["docker"] == {"cli": False, "context": None, "reachable": False}
    assert result["containers"] == []
    assert (result["clickhouse_ping"], result["signoz_present"]) == (True, True)
    notes = " ".join(result["notes"])
    assert "Docker CLI not found" in notes
    assert 'clickhouse_url = "http://127.0.0.1:8123"' in notes


def test_clickhouse_is_pinged_only_when_its_port_is_open(machine: Any) -> None:
    machine(ping=True)

    assert signoz_detect.detect()["clickhouse_ping"] is False


@pytest.mark.parametrize("ps", [1, -1], ids=["daemon-down", "timeout"])
def test_an_unreachable_daemon_is_reported_not_raised(machine: Any, ps: int) -> None:
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
    machine: Any, open_ports: tuple[int, ...], expected: str
) -> None:
    machine(open_ports=open_ports)

    result = signoz_detect.detect()

    assert result["signoz_present"] is False
    assert expected in " ".join(result["notes"])


def test_signoz_detect_command_prints_one_json_object(
    machine: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    machine([CLICKHOUSE])

    assert main(["signoz", "detect"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["signoz_present"] is True
    assert set(result) >= {"docker", "containers", "ports", "signoz_present", "notes"}


def test_the_docker_seams_find_and_run_commands() -> None:
    completed = signoz_detect._run_docker((sys.executable, "-c", "print('ok')"))

    assert (completed.returncode, completed.stdout) == (0, "ok\n")
    assert signoz_detect._docker_cli() == shutil.which("docker")


def test_port_open_sees_a_listener_on_loopback() -> None:
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]
        assert signoz_detect.port_open(port) is True
    assert signoz_detect.port_open(port) is False


class _Ping(BaseHTTPRequestHandler):
    body = b"Ok.\n"

    def do_GET(self) -> None:
        self.send_response(200 if self.path == "/ping" else 404)
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *_args: object) -> None:
        return


@pytest.fixture
def ping_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[type[_Ping]]:
    """An HTTP server on a free loopback port, standing in for ClickHouse on 8123."""
    handler = type("Ping", (_Ping,), {})
    server = HTTPServer(("127.0.0.1", 0), handler)
    monkeypatch.setattr(signoz_detect, "CLICKHOUSE_HTTP_PORT", server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield handler
    server.shutdown()
    server.server_close()


def test_clickhouse_answers_only_with_ok(ping_server: type[_Ping]) -> None:
    assert signoz_detect.clickhouse_answers() is True
    ping_server.body = b"<html>not ClickHouse</html>"
    assert signoz_detect.clickhouse_answers() is False


def test_clickhouse_answers_false_when_nothing_listens(monkeypatch: pytest.MonkeyPatch) -> None:
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]
    monkeypatch.setattr(signoz_detect, "CLICKHOUSE_HTTP_PORT", port)

    assert signoz_detect.clickhouse_answers() is False


# install-signoz.sh, driven with a fake `docker` and `agent-introspection` on PATH. The
# PATH keeps only system directories, so macOS runs it under its bash 3.2.

_FAKE_DOCKER = """#!/bin/sh
printf '%s\\n' "$*" >>"$FAKE_DOCKER_LOG"
case "$*" in
  "compose version" | "info") exit 0 ;;
  "compose version --short") printf '%s\\n' "$FAKE_COMPOSE_VERSION" ;;
  "context inspect --format {{.Endpoints.docker.Host}}") echo "unix:///var/run/docker.sock" ;;
  "ps -a --format {{.Names}} {{.Image}}") printf '%s' "$FAKE_CONTAINERS" ;;
  "compose ls --all --quiet") printf '%s' "$FAKE_PROJECTS" ;;
  "volume ls --quiet") printf '%s' "$FAKE_VOLUMES" ;;
  *) exit 99 ;;
esac
"""
_FAKE_CLI = """#!/bin/sh
[ "$1 $2" = "signoz detect" ] || exit 2
printf '%s\\n' "$FAKE_DETECT"
"""
_READ_ONLY_DOCKER = {
    "compose version",
    "compose version --short",
    "info",
    "context inspect --format {{.Endpoints.docker.Host}}",
    "ps -a --format {{.Names}} {{.Image}}",
    "compose ls --all --quiet",
    "volume ls --quiet",
}


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def detect_output(machine: Any, capsys: pytest.CaptureFixture[str]) -> Any:
    """Print `signoz detect` for a faked machine, as the install script reads it."""

    def render(*args: Any, **kwargs: Any) -> str:
        machine(*args, **kwargs)
        assert main(["signoz", "detect"]) == 0
        return capsys.readouterr().out.strip()

    return render


def _install(
    tmp_path: Path, *args: str, detect: str | None, **docker: str
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run install-signoz.sh; return the result and the docker commands it ran."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    _executable(fake_bin / "docker", _FAKE_DOCKER)
    if detect is not None:
        _executable(fake_bin / "agent-introspection", _FAKE_CLI)
    log = tmp_path / "docker.log"
    env = {
        "PATH": f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(tmp_path / "home"),
        "FAKE_DOCKER_LOG": str(log),
        "FAKE_DETECT": detect or "",
        "FAKE_CONTAINERS": docker.get("containers", ""),
        "FAKE_PROJECTS": docker.get("projects", ""),
        "FAKE_VOLUMES": docker.get("volumes", ""),
        "FAKE_COMPOSE_VERSION": docker.get("compose", "2.24.4"),
        **({"DOCKER_HOST": docker["host"]} if "host" in docker else {}),
    }
    result = subprocess.run(
        [str(INSTALL), *args], capture_output=True, text=True, env=env, timeout=60, check=False
    )
    return result, log.read_text().splitlines() if log.exists() else []


def test_install_dry_run_prints_the_plan_and_changes_nothing(
    tmp_path: Path, detect_output: Any
) -> None:
    result, docker_commands = _install(tmp_path, "--dry-run", detect=detect_output())

    assert result.returncode == 0, result.stderr
    default_dir = tmp_path / "home/.local/share/agent-introspection/signoz"
    assert f"into {default_dir}" in result.stdout
    assert "Plan (dry run: nothing is changed)" in result.stdout
    assert "foundryctl v0.3.0" in result.stdout
    assert "docker-compose.clickhouse-http.yaml up -d" in result.stdout
    assert 'clickhouse_url = "http://127.0.0.1:8123"' in result.stdout
    assert not (tmp_path / "home").exists()
    assert set(docker_commands) <= _READ_ONLY_DOCKER


@pytest.mark.parametrize(
    ("found", "finding"),
    [
        ({"rows": [CLICKHOUSE]}, "a SigNoz or ClickHouse is present"),
        ({"open_ports": (8080,)}, "port 8080 on this machine is in use"),
        ({"containers": "signoz-old signoz/signoz:v0.128.0\n"}, "container signoz-old"),
        ({"projects": "other\nsignoz\n"}, "Docker Compose project signoz"),
        ({"volumes": "signoz-clickhouse\n"}, "Docker volume signoz-clickhouse"),
    ],
    ids=["running", "port", "stopped-container", "compose-project", "volume"],
)
def test_install_refuses_an_existing_signoz_even_in_a_dry_run(
    tmp_path: Path, detect_output: Any, found: dict[str, Any], finding: str
) -> None:
    detect = detect_output(found.get("rows", ()), open_ports=found.get("open_ports", ()))
    docker = {key: found[key] for key in ("containers", "projects", "volumes") if key in found}
    result, docker_commands = _install(tmp_path, "--dry-run", detect=detect, **docker)

    assert result.returncode == 3
    assert finding in result.stderr
    assert "never reconfigures an existing SigNoz" in result.stderr
    assert "Plan" not in result.stdout
    assert set(docker_commands) <= _READ_ONLY_DOCKER


def test_install_checks_docker_itself_without_signoz_detect(tmp_path: Path) -> None:
    result, _ = _install(
        tmp_path, "--dry-run", detect=None, containers="signoz-clickhouse clickhouse/x:1\n"
    )

    assert "probing ports 4317 4318 8123 8080 directly" in result.stdout
    assert result.returncode == 3
    assert "container signoz-clickhouse" in result.stderr


def test_install_stops_on_a_non_empty_dir_or_a_remote_daemon(
    tmp_path: Path, detect_output: Any
) -> None:
    used = tmp_path / "used"
    used.mkdir()
    (used / "casting.yaml").write_text("")
    clean = detect_output()

    non_empty, _ = _install(tmp_path, "--dry-run", "--dir", str(used), detect=clean)
    remote, _ = _install(tmp_path, "--dry-run", detect=clean, host="tcp://10.0.0.5:2376")

    assert (non_empty.returncode, remote.returncode) == (1, 1)
    assert "is not empty" in non_empty.stderr
    assert "not on this machine" in remote.stderr


@pytest.mark.parametrize(
    ("compose", "accepted"),
    [
        ("5.1.2", True),
        ("v2.24.6-desktop.1", True),
        ("2.24.3", False),
        ("1.29.2", False),
        ("", False),
    ],
)
def test_install_needs_a_compose_that_can_replace_the_published_ports(
    tmp_path: Path, detect_output: Any, compose: str, accepted: bool
) -> None:
    result, _ = _install(tmp_path, "--dry-run", detect=detect_output(), compose=compose)

    assert result.returncode == (0 if accepted else 1), result.stderr
    if not accepted:
        assert "Docker Compose 2.24.4 or later is required" in result.stderr
        assert "Plan" not in result.stdout


@pytest.mark.parametrize("args", [["--tag", "latest"], ["--dir"], ["--bogus"]])
def test_install_rejects_bad_arguments(tmp_path: Path, args: list[str]) -> None:
    # A dry run: an argument that is wrongly accepted prints a plan, never downloads.
    result, docker_commands = _install(tmp_path, "--dry-run", *args, detect="")

    assert result.returncode == 2
    assert "Plan" not in result.stdout
    assert docker_commands == []

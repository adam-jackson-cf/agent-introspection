from __future__ import annotations

import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

from agent_introspection.cli import main
from tests.support.signoz_machine import CLICKHOUSE, FakeMachine

pytestmark = pytest.mark.boundary

ROOT = Path(__file__).resolve().parents[1]
SIGNOZ_SCRIPTS = ROOT / ".agents/skills/introspection-onboarding/scripts/signoz"
INSTALL = SIGNOZ_SCRIPTS / "install-signoz.sh"


@pytest.fixture
def machine(monkeypatch: pytest.MonkeyPatch) -> FakeMachine:
    """Fake this machine: Docker on PATH, every port free, ClickHouse silent."""
    return FakeMachine(monkeypatch)


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
def detect_output(machine: FakeMachine, capsys: pytest.CaptureFixture[str]) -> Any:
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
    assert "needs no [signoz] table" in result.stdout
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

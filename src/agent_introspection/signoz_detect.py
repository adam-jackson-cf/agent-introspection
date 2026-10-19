"""Read-only detection of a SigNoz and its ClickHouse on this machine.

``signoz detect`` reports what a SigNoz install would collide with: running containers
whose name or image mentions SigNoz or ClickHouse, the local ports a SigNoz publishes
(OTLP gRPC 4317, OTLP/HTTP 4318, ClickHouse HTTP 8123, the SigNoz UI 8080), and whether
ClickHouse answers ``/ping`` on 127.0.0.1:8123. A SigNoz on other ports is checked at
the ClickHouse URL and OTLP/HTTP endpoint given instead, the same values ``setup``
takes. It never starts, stops, or changes anything. A missing Docker CLI or a daemon
that does not answer is reported, never raised.
"""

from __future__ import annotations

import http.client
import json
import shutil
import socket
import subprocess
import urllib.request
from collections.abc import Sequence
from typing import Any, Final, cast
from urllib.parse import urlsplit

LOOPBACK: Final = "127.0.0.1"
CLICKHOUSE_HTTP_PORT: Final = 8123
# OTLP gRPC, OTLP/HTTP, ClickHouse HTTP, and the SigNoz UI.
PORTS: Final = (4317, 4318, CLICKHOUSE_HTTP_PORT, 8080)
INSTALL_SCRIPT: Final = ".agents/skills/introspection-onboarding/scripts/signoz/install-signoz.sh"
_MARKERS: Final = ("signoz", "clickhouse")
_CLICKHOUSE_SERVER_IMAGE: Final = "clickhouse-server"
_DOCKER_TIMEOUT_SECONDS: Final = 10
_CONNECT_TIMEOUT_SECONDS: Final = 0.5
_PING_TIMEOUT_SECONDS: Final = 2

type Container = dict[str, Any]


def _docker_cli() -> str | None:
    return shutil.which("docker")


def _run_docker(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, capture_output=True, text=True, timeout=_DOCKER_TIMEOUT_SECONDS, check=False
    )


def _docker_output(docker: str, *args: str) -> str | None:
    """Return the output of ``docker ARGS``, or None when it fails or times out."""
    try:
        completed = _run_docker((docker, *args))
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout if completed.returncode == 0 else None


def _container(row: dict[str, Any]) -> Container:
    ports = str(row.get("Ports") or "")
    return {
        "name": str(row.get("Names") or ""),
        "image": str(row.get("Image") or ""),
        "ports": [port for port in ports.split(", ") if port],
    }


def signoz_containers(listing: str) -> list[Container]:
    """Return the containers in ``docker ps`` JSON lines that mention SigNoz or ClickHouse."""
    rows: list[Any] = []
    for line in listing.splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    containers = [_container(row) for row in rows if isinstance(row, dict)]
    return [
        container
        for container in containers
        if any(marker in f"{container['name']} {container['image']}".lower() for marker in _MARKERS)
    ]


def port_open(port: int, host: str = LOOPBACK) -> bool:
    """Return whether something accepts TCP connections on ``host``:``port``."""
    try:
        with socket.create_connection((host, port), timeout=_CONNECT_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


def default_clickhouse_url() -> str:
    return f"http://{LOOPBACK}:{CLICKHOUSE_HTTP_PORT}"


def clickhouse_answers(url: str | None = None) -> bool:
    """Return whether ClickHouse answers ``GET /ping`` with ``Ok.`` at ``url``.

    ``url`` defaults to 127.0.0.1:8123, the default ``[signoz] clickhouse_url``.
    """
    # No proxy: a loopback address must never be sent through one.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = (url or default_clickhouse_url()).rstrip("/")
    try:
        with opener.open(f"{base}/ping", timeout=_PING_TIMEOUT_SECONDS) as response:
            return cast(bytes, response.read(64)).strip() == b"Ok."
    except (OSError, ValueError, http.client.HTTPException):
        return False


def endpoint_open(url: str) -> bool:
    """Return whether something accepts TCP connections at an OTLP/HTTP endpoint URL."""
    try:
        parts = urlsplit(url if "://" in url else f"http://{url}")
        port = parts.port or 80
    except ValueError:
        return False
    return port_open(port, parts.hostname or LOOPBACK)


def _docker_state() -> tuple[dict[str, Any], list[Container]]:
    docker = _docker_cli()
    if docker is None:
        return {"cli": False, "context": None, "reachable": False}, []
    context = (_docker_output(docker, "context", "show") or "").strip() or None
    listing = _docker_output(docker, "ps", "--format", "{{json .}}")
    state = {"cli": True, "context": context, "reachable": listing is not None}
    return state, signoz_containers(listing or "")


def _docker_notes(docker: dict[str, Any]) -> list[str]:
    if not docker["cli"]:
        return ["Docker CLI not found: container checks were skipped."]
    if not docker["reachable"]:
        context = docker["context"] or "the current context"
        return [
            f"The Docker daemon ({context}) did not answer `docker ps`: "
            "container checks were skipped."
        ]
    return []


def _clickhouse_notes(containers: list[Container], ping: bool, url: str | None) -> list[str]:
    if ping and url:
        return [f"ClickHouse answers at {url}: pass it to `setup` as --clickhouse-url."]
    if ping:
        return [
            f"ClickHouse answers on {default_clickhouse_url()}, the default "
            "[signoz] clickhouse_url."
        ]
    if url:
        return [
            f"ClickHouse does not answer /ping at {url}: check the URL, and that it is "
            "SigNoz's ClickHouse HTTP interface, not its native port."
        ]
    return [
        f"ClickHouse container {container['name']} does not answer on "
        f"{LOOPBACK}:{CLICKHOUSE_HTTP_PORT}: publish its HTTP port on loopback with the "
        "opt-in Compose override in the onboarding skill's "
        "scripts/signoz/docker-compose.clickhouse-http.yaml, or set [signoz] clickhouse_url "
        f"to a local name your container runtime resolves (such as OrbStack's "
        f"http://{container['name']}.orb.local:{CLICKHOUSE_HTTP_PORT})."
        for container in containers
        if _CLICKHOUSE_SERVER_IMAGE in container["image"].lower()
    ]


def _endpoint_notes(endpoint: str | None, open_: bool | None) -> list[str]:
    if endpoint is None:
        return []
    if open_:
        return [
            f"The collector accepts connections at {endpoint}: pass it to `setup` as "
            "--otlp-endpoint."
        ]
    return [
        f"Nothing accepts connections at {endpoint}: check the collector's OTLP/HTTP "
        "endpoint (SigNoz's default port is 4318)."
    ]


def _install_notes(present: bool, ports: dict[str, bool], searched: bool) -> list[str]:
    if present:
        return [
            "A SigNoz or ClickHouse is present: connect to it and never reconfigure it "
            "(`agent-introspection doctor` checks it); the guided install refuses."
        ]
    busy = [port for port, used in ports.items() if used]
    if busy:
        return [
            f"Ports in use: {', '.join(busy)}. A new SigNoz needs ports "
            f"{', '.join(ports)} free; the guided install refuses."
        ]
    elsewhere = (
        ""
        if searched
        else " A SigNoz on other ports or another machine is not found this way: rerun "
        "with --clickhouse-url and --otlp-endpoint."
    )
    return [
        "No SigNoz or ClickHouse found and every SigNoz port is free: the opt-in guided "
        f"install ({INSTALL_SCRIPT}) can run.{elsewhere}"
    ]


def detect(clickhouse_url: str | None = None, otlp_endpoint: str | None = None) -> dict[str, Any]:
    """Report SigNoz and ClickHouse containers and local ports; change nothing.

    ``signoz_present`` is true when a container that mentions SigNoz or ClickHouse is
    running, or ClickHouse answers ``/ping``: at ``clickhouse_url`` when given, else on
    127.0.0.1:8123. ``otlp_open`` says whether ``otlp_endpoint`` accepts connections,
    and is None when no endpoint is given.
    """
    docker, containers = _docker_state()
    ports = {str(port): port_open(port) for port in PORTS}
    if clickhouse_url:
        ping = clickhouse_answers(clickhouse_url)
    else:
        ping = ports[str(CLICKHOUSE_HTTP_PORT)] and clickhouse_answers()
    otlp_open = endpoint_open(otlp_endpoint) if otlp_endpoint else None
    present = bool(containers) or ping
    return {
        "docker": docker,
        "containers": containers,
        "ports": ports,
        "clickhouse_url": clickhouse_url or default_clickhouse_url(),
        "clickhouse_ping": ping,
        "otlp_endpoint": otlp_endpoint,
        "otlp_open": otlp_open,
        "signoz_present": present,
        "notes": [
            *_docker_notes(docker),
            *_clickhouse_notes(containers, ping, clickhouse_url),
            *_endpoint_notes(otlp_endpoint, otlp_open),
            *_install_notes(present, ports, bool(clickhouse_url or otlp_endpoint)),
        ],
    }

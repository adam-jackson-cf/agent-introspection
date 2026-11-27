from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence

import pytest

from agent_introspection import signoz_detect

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

type DockerRun = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def docker_seam(
    rows: Sequence[dict[str, str]] = (), *, ps: int = 0, context: str = "orbstack"
) -> DockerRun:
    def run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        if command[1:] == ("context", "show"):
            return subprocess.CompletedProcess(command, 0, f"{context}\n", "")
        assert command[1:] == ("ps", "--format", "{{json .}}")
        if ps < 0:
            raise subprocess.TimeoutExpired(command, 10)
        lines = [json.dumps(row) for row in rows] + ["not json", "[1]"]
        return subprocess.CompletedProcess(command, ps, "\n".join(lines) + "\n", "")

    return run


class FakeMachine:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.monkeypatch = monkeypatch

    def __call__(
        self,
        rows: Sequence[dict[str, str]] = (),
        *,
        docker: str | None = "/usr/local/bin/docker",
        ps: int = 0,
        open_ports: Sequence[int] = (),
        ping: bool = False,
    ) -> None:
        self.monkeypatch.setattr(signoz_detect, "_docker_cli", lambda: docker)
        self.monkeypatch.setattr(signoz_detect, "_run_docker", docker_seam(rows, ps=ps))
        self.monkeypatch.setattr(
            signoz_detect, "port_open", lambda port, host="127.0.0.1": port in open_ports
        )
        self.monkeypatch.setattr(signoz_detect, "clickhouse_answers", lambda url=None: ping)

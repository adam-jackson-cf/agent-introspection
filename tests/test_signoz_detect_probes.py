from __future__ import annotations

import shutil
import socket
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from agent_introspection import signoz_detect

pytestmark = pytest.mark.boundary


def test_endpoint_open_reads_host_and_port() -> None:
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]
        assert signoz_detect.endpoint_open(f"http://127.0.0.1:{port}/v1/logs") is True
        assert signoz_detect.endpoint_open(f"127.0.0.1:{port}") is True
    assert signoz_detect.endpoint_open("http://127.0.0.1:notaport") is False


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
def ping_server() -> Iterator[tuple[type[_Ping], str]]:
    handler = type("Ping", (_Ping,), {})
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield handler, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_clickhouse_answers_only_with_ok(ping_server: tuple[type[_Ping], str]) -> None:
    handler, url = ping_server
    assert signoz_detect.clickhouse_answers(url) is True
    assert signoz_detect.clickhouse_answers(f"{url}/") is True
    handler.body = b"<html>not ClickHouse</html>"
    assert signoz_detect.clickhouse_answers(url) is False
    assert signoz_detect.default_clickhouse_url() == "http://127.0.0.1:8123"


def test_clickhouse_answers_false_when_nothing_listens() -> None:
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]

    assert signoz_detect.clickhouse_answers(f"http://127.0.0.1:{port}") is False

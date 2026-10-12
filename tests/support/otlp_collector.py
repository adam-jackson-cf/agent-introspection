"""A local OTLP/HTTP logs collector and spool record helpers for the producer tests."""

import json
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from agent_introspection import hooks

OCCURRED = "2026-09-30T12:00:00.123456Z"


class Collector:
    """Records every POSTed request and answers from a script (then ``default``)."""

    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []
        self.script: list[tuple[int, str]] = []
        self.default: tuple[int, str] = (200, "{}")
        self.delay = 0.0
        self.on_request: Callable[[], None] | None = None
        collector = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers["Content-Length"])
                collector.bodies.append(json.loads(self.rfile.read(length)))
                if collector.on_request is not None:
                    collector.on_request()
                time.sleep(collector.delay)
                status, text = collector.script.pop(0) if collector.script else collector.default
                payload = text.encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    @property
    def url(self) -> str:
        return f"{self.base}/v1/logs"

    @property
    def records(self) -> list[dict[str, Any]]:
        return [
            json.loads(entry["body"]["stringValue"])
            for body in self.bodies
            for entry in body["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
        ]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def hook_record(index: int) -> dict[str, Any]:
    return hooks.record(("claude-code", "s1", "tool_call"), OCCURRED, f"key-{index}", {"n": index})


def spool_records(inbox: Path, count: int) -> list[dict[str, Any]]:
    items = [hook_record(index) for index in range(count)]
    for item in items:
        hooks.write(item, inbox)
    return items


def names(directory: Path) -> set[str]:
    return {path.name for path in directory.glob("*.json")}

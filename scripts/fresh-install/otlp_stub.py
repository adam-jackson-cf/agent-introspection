"""A stand-in SigNoz collector for the fresh-install sandbox.

It answers OTLP/HTTP JSON on loopback the way the SigNoz collector does, and writes
each log record into the sandbox ClickHouse's ``signoz_logs.distributed_logs_v2`` (the
stub table ``signoz-stub.sql`` creates), so hook records travel the whole path: hook,
spool, collector, ClickHouse, ``facts sync``. Traces and metrics are accepted and
counted, not stored. Every request is counted by path in ``<state>/collector.json``.

Usage: ``python3 -I otlp_stub.py PORT CLICKHOUSE_URL STATE_DIR``.
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

INSERT = "INSERT INTO signoz_logs.distributed_logs_v2 FORMAT JSONEachRow"


def _value(item: dict[str, Any]) -> object:
    value = item.get("value", {})
    for key in ("stringValue", "boolValue", "doubleValue"):
        if key in value:
            return value[key]
    if "intValue" in value:
        return float(value["intValue"])
    return json.dumps(value)


def _attributes(items: list[dict[str, Any]]) -> dict[str, dict[str, object]]:
    typed: dict[str, dict[str, object]] = {"string": {}, "number": {}, "bool": {}}
    for item in items:
        value = _value(item)
        kind = (
            "bool"
            if isinstance(value, bool)
            else "number"
            if isinstance(value, float)
            else "string"
        )
        typed[kind][item["key"]] = value
    return typed


def _scopes(body: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Return each scope's logs with its resource."""
    return [
        (resource_logs.get("resource", {}), scope)
        for resource_logs in body.get("resourceLogs", [])
        for scope in resource_logs.get("scopeLogs", [])
    ]


def _records(body: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Return each log record with its resource."""
    return [
        (resource, record)
        for resource, scope in _scopes(body)
        for record in scope.get("logRecords", [])
    ]


def rows(body: dict[str, Any]) -> list[dict[str, object]]:
    """Return the SigNoz log rows an OTLP/HTTP JSON logs request carries."""
    found: list[dict[str, object]] = []
    for resource_part, record in _records(body):
        resource = _attributes(resource_part.get("attributes", []))
        attributes = _attributes(record.get("attributes", []))
        stamp = int(record.get("timeUnixNano") or record.get("observedTimeUnixNano") or 0)
        found.append({
            "timestamp": stamp,
            "observed_timestamp": int(record.get("observedTimeUnixNano") or stamp),
            "id": f"{stamp}-{len(found)}",
            "trace_id": record.get("traceId", ""),
            "span_id": record.get("spanId", ""),
            "severity_text": record.get("severityText", ""),
            "body": str(_value({"value": record.get("body", {})})),
            "attributes_string": attributes["string"],
            "attributes_number": attributes["number"],
            "attributes_bool": attributes["bool"],
            "resources_string": resource["string"],
            "resource": {**resource["string"], **resource["number"]},
        })
    return found


def main(port: int, clickhouse: str, state: Path) -> None:
    """Serve until interrupted."""
    counts: dict[str, int] = {}

    class Handler(BaseHTTPRequestHandler):
        def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
            return

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            payload = self.rfile.read(length)
            counts[self.path] = counts.get(self.path, 0) + 1
            status = 200
            if self.path == "/v1/logs":
                lines = "\n".join(json.dumps(row) for row in rows(json.loads(payload or b"{}")))
                if lines:
                    query = urllib.parse.quote(INSERT)
                    request = urllib.request.Request(
                        f"{clickhouse}/?query={query}", data=lines.encode(), method="POST"
                    )
                    try:
                        with urllib.request.urlopen(request, timeout=10):
                            pass
                    except OSError:
                        status = 503
            (state / "collector.json").write_text(json.dumps(counts, sort_keys=True))
            answer = b"{}"
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(answer)))
            self.end_headers()
            self.wfile.write(answer)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main(int(sys.argv[1]), sys.argv[2].rstrip("/"), Path(sys.argv[3]))

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import parse_qs, urlsplit

import pytest

from agent_introspection.cli import main
from agent_introspection.cli_dashboard import URL_VARIABLE

pytestmark = pytest.mark.boundary

REGISTRY = {
    "signals": [
        {
            "signal": "friction.interrupt",
            "view": "friction",
            "view_title": "Friction",
            "title": "Interrupted tasks",
            "question": "How often are tasks interrupted?",
            "unit": "%",
        },
        {
            "signal": "intervene.findings",
            "view": "interventions",
            "view_title": "Findings and fixes",
            "title": "Active findings",
            "question": "Which failures recur?",
            "unit": "count",
        },
    ],
    "harnesses": [
        {"harness": "claude-code", "label": "Claude Code", "enabled": 1},
        {"harness": "codex_exec", "label": "Codex exec", "enabled": 0},
    ],
    "hidden": [],
    "features": {"intervention_outcomes": False},
}
FRICTION = {
    "view": "friction",
    "data": {"rates": [{"day": f"2026-11-{day:02}"} for day in range(1, 6)]},
    "errors": {},
    "asOf": {"at": "2026-11-18T10:00:00Z", "stale": False, "historyStale": False, "failing": []},
    "contributions": {
        "friction.interrupt": {
            "claude-code": {"state": "route missing", "rows": 0, "from": None},
            "oh-my-pi": {"state": "rows", "rows": 4, "from": None},
        }
    },
}


class _Dashboard(BaseHTTPRequestHandler):
    seen: ClassVar[list[dict[str, Any]]] = []

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        _Dashboard.seen.append({"path": url.path, "host": self.headers["host"], **query})
        if url.path == "/api/registry":
            self._send(200, REGISTRY)
        elif query.get("view") == "friction":
            self._send(200, FRICTION)
        elif query.get("view") == "interventions":
            self._send(503, {"error": "ClickHouse is down"})
        else:
            self._send(400, {"error": "view must be a known view"})

    def _send(self, status: int, body: object) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args: object) -> None:
        return


@pytest.fixture
def dashboard(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Dashboard)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    monkeypatch.setenv(URL_VARIABLE, f"http://127.0.0.1:{server.server_port}")
    _Dashboard.seen = []
    try:
        yield _Dashboard.seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> Any:
    assert main(["dashboard", *argv]) == 0
    return json.loads(capsys.readouterr().out)


def test_a_view_is_read_with_its_window_and_cut_to_the_requested_rows(
    dashboard: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    window = ["--start", "2026-11-01T00:00:00Z", "--end", "2026-11-08T00:00:00Z"]
    result = _run(capsys, "view", "friction", *window, "--harness", "claude-code", "--rows", "2")

    assert dashboard[-1] == {
        "path": "/api/view",
        "host": dashboard[-1]["host"],
        "view": "friction",
        "start": "2026-11-01T00:00:00Z",
        "end": "2026-11-08T00:00:00Z",
        "harness": "claude-code",
    }
    assert [row["day"] for row in result["data"]["rates"]] == ["2026-11-01", "2026-11-02"]
    assert result["truncated"] == {"rates": 5}


def test_views_list_each_views_questions_and_the_enabled_harnesses(
    dashboard: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    result = _run(capsys, "views")

    assert [(entry["view"], entry["title"]) for entry in result["views"]] == [
        ("friction", "Friction"),
        ("interventions", "Findings and fixes"),
    ]
    assert result["views"][0]["signals"][0]["question"] == "How often are tasks interrupted?"
    assert result["harnesses"] == ["claude-code"]
    assert result["features"] == {"intervention_outcomes": False}


def test_the_overview_flags_missing_routes_and_keeps_going_past_a_failed_view(
    dashboard: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    result = _run(capsys, "overview", "--days", "7")

    friction, interventions = result["views"]
    assert friction["route_missing"] == ["friction.interrupt (claude-code)"]
    assert friction["datasets"] == {"rates": 5}
    assert "ClickHouse is down" in interventions["failed"]
    assert result["findings_total"] == 0


def test_an_unknown_view_or_a_bad_window_is_a_validation_error(
    dashboard: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    for argv in (["view", "nope"], ["view", "friction", "--days", "93"]):
        with pytest.raises(SystemExit) as raised:
            main(["dashboard", *argv])
        assert raised.value.code == 50
    assert "view must be a known view" in capsys.readouterr().err


def test_no_running_dashboard_names_the_command_that_starts_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Dashboard)
    port = server.server_port
    server.server_close()
    monkeypatch.setenv(URL_VARIABLE, f"http://127.0.0.1:{port}")

    with pytest.raises(SystemExit) as raised:
        main(["dashboard", "views"])

    assert raised.value.code == 60
    assert "dashboard install" in capsys.readouterr().err


def test_without_an_override_the_configured_port_is_read(
    dashboard: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    port = urlsplit(os.environ[URL_VARIABLE]).port
    monkeypatch.delenv(URL_VARIABLE)
    config = tmp_path / "config.toml"
    config.write_text(f"[dashboard]\nport = {port}\n", encoding="utf-8")

    assert main(["--config", str(config), "dashboard", "views"]) == 0

    assert json.loads(capsys.readouterr().out)["harnesses"] == ["claude-code"]
    assert dashboard[-1]["host"] == f"127.0.0.1:{port}"


def test_a_dashboard_address_off_this_machine_is_refused_before_any_request(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(URL_VARIABLE, "http://dashboard.example.com:4173")

    with pytest.raises(SystemExit) as raised:
        main(["dashboard", "views"])

    assert raised.value.code == 50
    assert "on loopback" in capsys.readouterr().err

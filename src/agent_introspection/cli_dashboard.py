from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import ProxyHandler, build_opener

from agent_introspection import dashboard_service
from agent_introspection.cli_common import app_config

URL_VARIABLE: Final = "INTROSPECTION_DASHBOARD_URL"
DASHBOARD_ADDRESS: Final = "127.0.0.1"
TIMEOUT_SECONDS: Final = 120
MAX_DAYS: Final = 92
DEFAULT_DAYS: Final = 7
OVERVIEW_WORKERS: Final = 4
OVERVIEW_FINDINGS: Final = 20
_OPENER = build_opener(ProxyHandler({}))


class DashboardUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Window:
    start: str
    end: str
    harness: str = ""


def _utc(instant: datetime) -> str:
    return instant.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def window_for(days: int | None, start: str | None, end: str | None, harness: str) -> Window:
    if start is not None or end is not None:
        if start is None or end is None or days is not None:
            raise ValueError("give --start and --end together, without --days")
        return Window(start, end, harness)
    span = DEFAULT_DAYS if days is None else days
    if not 1 <= span <= MAX_DAYS:
        raise ValueError(f"--days must be between 1 and {MAX_DAYS}")
    now = datetime.now(UTC)
    return Window(_utc(now - timedelta(days=span)), _utc(now), harness)


def dashboard_url(port: int) -> str:
    url = os.environ.get(URL_VARIABLE, "").rstrip("/") or f"http://127.0.0.1:{port}"
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.hostname != DASHBOARD_ADDRESS:
        raise ValueError(
            f"{URL_VARIABLE} must be an http URL on {DASHBOARD_ADDRESS}, the only address "
            f"the dashboard serves, not {url}"
        )
    return url


def _get(base: str, path: str, params: dict[str, str] | None = None) -> Any:
    url = f"{base}{path}" + (f"?{urlencode(params)}" if params else "")
    try:
        with _OPENER.open(url, timeout=TIMEOUT_SECONDS) as response:
            return json.load(response)
    except HTTPError as exc:
        with exc:
            try:
                message = str(json.load(exc).get("error", exc.reason))
            except ValueError:
                message = str(exc.reason)
        if exc.code == 400:
            raise ValueError(f"dashboard refused {path}: {message}") from exc
        raise RuntimeError(f"dashboard {path} failed ({exc.code}): {message}") from exc
    except URLError as exc:
        raise DashboardUnavailableError(
            f"no dashboard at {base} ({exc.reason}); on the SigNoz host run "
            "`agent-introspection dashboard install --repo <checkout>`"
        ) from exc


def _view_titles(registry: dict[str, Any]) -> dict[str, str]:
    return {signal["view"]: signal["view_title"] for signal in registry["signals"]}


def views(base: str) -> dict[str, Any]:
    registry = _get(base, "/api/registry")
    listed: dict[str, dict[str, Any]] = {
        view: {"view": view, "title": title, "signals": []}
        for view, title in _view_titles(registry).items()
    }
    hidden = {entry["signal"] for entry in registry["hidden"]}
    for signal in registry["signals"]:
        if signal["signal"] in hidden:
            continue
        listed[signal["view"]]["signals"].append({
            key: signal[key] for key in ("signal", "title", "question", "unit")
        })
    return {
        "views": list(listed.values()),
        "features": registry["features"],
        "harnesses": [
            entry["harness"] for entry in registry["harnesses"] if int(entry["enabled"]) == 1
        ],
        "hidden": [
            {key: entry[key] for key in ("signal", "view", "missing", "note")}
            for entry in registry["hidden"]
        ],
    }


def _trimmed(response: dict[str, Any], rows: int | None) -> dict[str, Any]:
    if rows is None:
        return response
    data = response["data"]
    truncated = {name: len(found) for name, found in data.items() if len(found) > rows}
    return {
        **response,
        "data": {name: found[:rows] for name, found in data.items()},
        "truncated": truncated,
    }


def view(base: str, view_id: str, window: Window, rows: int | None) -> dict[str, Any]:
    response = _get(
        base,
        "/api/view",
        {"view": view_id, "start": window.start, "end": window.end, "harness": window.harness},
    )
    return _trimmed(response, rows)


def session(base: str, harness: str, session_id: str, rows: int | None) -> dict[str, Any]:
    return _trimmed(_get(base, "/api/session", {"harness": harness, "session": session_id}), rows)


def _missing_routes(contributions: dict[str, dict[str, dict[str, Any]]]) -> list[str]:
    return sorted(
        f"{signal} ({harness})"
        for signal, by_harness in contributions.items()
        for harness, contribution in by_harness.items()
        if contribution["state"] == "route missing"
    )


def _digest(base: str, view_id: str, title: str, window: Window) -> dict[str, Any]:
    try:
        response = view(base, view_id, window, None)
    except (ValueError, RuntimeError) as exc:
        return {"view": view_id, "title": title, "failed": str(exc)}
    return {
        "view": view_id,
        "title": title,
        "datasets": {name: len(found) for name, found in response["data"].items()},
        "errors": response["errors"],
        "route_missing": _missing_routes(response["contributions"]),
        "as_of": response["asOf"],
        "findings": response["data"].get("findings", []),
    }


def overview(base: str, window: Window) -> dict[str, Any]:
    registry = _get(base, "/api/registry")
    titles = _view_titles(registry)
    with ThreadPoolExecutor(max_workers=OVERVIEW_WORKERS) as pool:
        digests = list(
            pool.map(lambda item: _digest(base, item[0], item[1], window), titles.items())
        )
    findings = [row for digest in digests for row in digest.pop("findings", [])]
    return {
        "window": {"start": window.start, "end": window.end, "harness": window.harness},
        "features": registry["features"],
        "views": digests,
        "findings": findings[:OVERVIEW_FINDINGS],
        "findings_total": len(findings),
    }


def _window_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--days",
        type=int,
        help=f"the range ending now, in days (default {DEFAULT_DAYS}, at most {MAX_DAYS})",
    )
    parser.add_argument("--start", help="range start, RFC 3339 UTC (with --end)")
    parser.add_argument("--end", help="range end, RFC 3339 UTC (with --start)")
    parser.add_argument("--harness", default="", help="one enabled harness (default: All)")


def _row_count(value: str) -> int:
    rows = int(value)
    if rows < 0:
        raise argparse.ArgumentTypeError("--rows must be 0 or more")
    return rows


def _rows_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--rows", type=_row_count, help="keep the first N rows of each dataset (default: all)"
    )


def add_dashboard_parser(commands: Any) -> None:
    dashboard = commands.add_parser(
        "dashboard",
        help="run the dashboard as a user service, and read what its views show as JSON",
    ).add_subparsers(dest="dashboard_command", required=True)
    install = dashboard.add_parser(
        "install",
        help="build the dashboard in a checkout and (re)start its service on 127.0.0.1 at "
        "[dashboard] port (default 4173)",
    )
    install.add_argument(
        "--repo",
        default=".",
        help="the agent-introspection checkout to build and serve from (default: here)",
    )
    dashboard.add_parser("status", help="whether the dashboard service is installed and running")
    dashboard.add_parser("remove", help="stop the dashboard service and delete its file")
    dashboard.add_parser(
        "views", help="every view, the questions its signals answer, and the features turned on"
    )
    one = dashboard.add_parser("view", help="one view's datasets, as the dashboard shows them")
    one.add_argument("view", help="a view id from `dashboard views`")
    _window_arguments(one)
    _rows_argument(one)
    overview = dashboard.add_parser(
        "overview",
        help="every view's dataset sizes, failed queries, missing routes and freshness, "
        "and the active findings",
    )
    _window_arguments(overview)
    session = dashboard.add_parser("session", help="one session's drill-down")
    session.add_argument("harness")
    session.add_argument("session")
    _rows_argument(session)


def _window(args: argparse.Namespace) -> Window:
    return window_for(args.days, args.start, args.end, args.harness)


def _base(args: argparse.Namespace) -> str:
    return dashboard_url(app_config(args).dashboard.port)


def dashboard_command(args: argparse.Namespace) -> dict[str, Any]:
    reads: dict[str, Callable[[], dict[str, Any]]] = {
        "status": dashboard_service.status,
        "remove": dashboard_service.remove,
        "views": lambda: views(_base(args)),
        "view": lambda: view(_base(args), args.view, _window(args), args.rows),
        "overview": lambda: overview(_base(args), _window(args)),
        "session": lambda: session(_base(args), args.harness, args.session, args.rows),
    }
    if args.dashboard_command in reads:
        return reads[args.dashboard_command]()
    config_path = Path(args.config).expanduser().resolve() if args.config else None
    return dashboard_service.install(app_config(args), Path(args.repo), config_path)

"""Read what the dashboard shows, as JSON, from the running dashboard's API.

The dashboard server (``dashboard/server.ts``) owns every view's queries and their
post-processing. These functions read its ``/api`` routes on loopback, so an agent
sees the same datasets the page renders without driving a browser, and repeat reads
hit the server's 10-minute caches. The dashboard service (``dashboard install``) must
be running on this machine, which is the SigNoz host. ``INTROSPECTION_DASHBOARD_URL``
names another loopback address, such as a server run by hand.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import ProxyHandler, build_opener

from agent_introspection import dashboard_service
from agent_introspection.config import is_local_host

DEFAULT_URL: Final = f"http://127.0.0.1:{dashboard_service.PORT}"
URL_VARIABLE: Final = "INTROSPECTION_DASHBOARD_URL"
# A cold view (no cached route counts) can take tens of seconds on a large store.
TIMEOUT_SECONDS: Final = 120
# The server refuses a wider range.
MAX_DAYS: Final = 92
DEFAULT_DAYS: Final = 7
# Views the overview reads at once; each runs its own ClickHouse batch.
OVERVIEW_WORKERS: Final = 4
OVERVIEW_FINDINGS: Final = 20
# Loopback only: a proxy from the environment must never see these requests.
_OPENER = build_opener(ProxyHandler({}))


class DashboardUnavailableError(RuntimeError):
    """Raised when no dashboard answers on this machine's loopback port."""


@dataclass(frozen=True, slots=True)
class Window:
    """A view's filters: the UTC range (RFC 3339) and a harness, empty for All."""

    start: str
    end: str
    harness: str = ""


def _utc(instant: datetime) -> str:
    return instant.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def window_for(days: int | None, start: str | None, end: str | None, harness: str) -> Window:
    """Return the window ending now over ``days``, or the explicit ``start``/``end``."""
    if start is not None or end is not None:
        if start is None or end is None or days is not None:
            raise ValueError("give --start and --end together, without --days")
        return Window(start, end, harness)
    span = DEFAULT_DAYS if days is None else days
    if not 1 <= span <= MAX_DAYS:
        raise ValueError(f"--days must be between 1 and {MAX_DAYS}")
    now = datetime.now(UTC)
    return Window(_utc(now - timedelta(days=span)), _utc(now), harness)


def dashboard_url() -> str:
    """Return the dashboard's base URL: ``INTROSPECTION_DASHBOARD_URL``, else the service's."""
    url = os.environ.get(URL_VARIABLE, "").rstrip("/") or DEFAULT_URL
    parts = urlsplit(url)
    if parts.scheme != "http" or not parts.hostname or not is_local_host(parts.hostname):
        raise ValueError(f"{URL_VARIABLE} must be an http URL on loopback, not {url}")
    return url


def _get(path: str, params: dict[str, str] | None = None) -> Any:
    base = dashboard_url()
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
    """Each view the registry's signals belong to, with its title, in registry order."""
    return {signal["view"]: signal["view_title"] for signal in registry["signals"]}


def views() -> dict[str, Any]:
    """Return every view with the questions its signals answer, and this machine's scope.

    ``features`` names the experimental features the config turns on; a view leaves out
    the panels of one that is off. ``hidden`` lists signals an enabled harness cannot
    produce, which the page leaves out too.
    """
    registry = _get("/api/registry")
    listed: dict[str, dict[str, Any]] = {
        view: {"view": view, "title": title, "signals": []}
        for view, title in _view_titles(registry).items()
    }
    for signal in registry["signals"]:
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
    """Keep the first ``rows`` rows of each dataset; ``truncated`` gives cut totals."""
    if rows is None:
        return response
    data = response["data"]
    truncated = {name: len(found) for name, found in data.items() if len(found) > rows}
    return {
        **response,
        "data": {name: found[:rows] for name, found in data.items()},
        "truncated": truncated,
    }


def view(view_id: str, window: Window, rows: int | None) -> dict[str, Any]:
    """Return one view's response: its datasets, failed queries, freshness and chips."""
    response = _get(
        "/api/view",
        {"view": view_id, "start": window.start, "end": window.end, "harness": window.harness},
    )
    return _trimmed(response, rows)


def session(harness: str, session_id: str, rows: int | None) -> dict[str, Any]:
    """Return one session's drill-down: its tasks, usage, tool calls and signals."""
    return _trimmed(_get("/api/session", {"harness": harness, "session": session_id}), rows)


def _missing_routes(contributions: dict[str, dict[str, dict[str, Any]]]) -> list[str]:
    """Signals with no rows from a harness that is otherwise active: a possible break."""
    return sorted(
        f"{signal} ({harness})"
        for signal, by_harness in contributions.items()
        for harness, contribution in by_harness.items()
        if contribution["state"] == "route missing"
    )


def _digest(view_id: str, title: str, window: Window) -> dict[str, Any]:
    """One view's summary: dataset sizes, failed queries, broken routes and freshness."""
    try:
        response = view(view_id, window, None)
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


def overview(window: Window) -> dict[str, Any]:
    """Read every view at once and return what needs attention across the dashboard.

    Per view: dataset sizes, failed queries, routes missing, and data freshness. Across
    views: the active findings ``facts sync`` promoted, the actionable ones first.
    """
    registry = _get("/api/registry")
    titles = _view_titles(registry)
    with ThreadPoolExecutor(max_workers=OVERVIEW_WORKERS) as pool:
        digests = list(pool.map(lambda item: _digest(item[0], item[1], window), titles.items()))
    findings = [row for digest in digests for row in digest.pop("findings", [])]
    return {
        "window": {"start": window.start, "end": window.end, "harness": window.harness},
        "features": registry["features"],
        "views": digests,
        "findings": findings[:OVERVIEW_FINDINGS],
        "findings_total": len(findings),
    }

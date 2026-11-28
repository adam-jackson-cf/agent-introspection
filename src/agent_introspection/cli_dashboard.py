"""Dashboard subcommands of the Agent Introspection CLI."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent_introspection import dashboard_api, dashboard_service
from agent_introspection.cli_common import app_config


def _window_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--days",
        type=int,
        help=f"the range ending now, in days (default {dashboard_api.DEFAULT_DAYS}, "
        f"at most {dashboard_api.MAX_DAYS})",
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
        help="build the dashboard in a checkout and (re)start its service on 127.0.0.1:4173",
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


def _window(args: argparse.Namespace) -> dashboard_api.Window:
    return dashboard_api.window_for(args.days, args.start, args.end, args.harness)


def dashboard_command(args: argparse.Namespace) -> dict[str, Any]:
    reads: dict[str, Callable[[], dict[str, Any]]] = {
        "status": dashboard_service.status,
        "remove": dashboard_service.remove,
        "views": dashboard_api.views,
        "view": lambda: dashboard_api.view(args.view, _window(args), args.rows),
        "overview": lambda: dashboard_api.overview(_window(args)),
        "session": lambda: dashboard_api.session(args.harness, args.session, args.rows),
    }
    if args.dashboard_command in reads:
        return reads[args.dashboard_command]()
    config_path = Path(args.config).expanduser().resolve() if args.config else None
    return dashboard_service.install(app_config(args), Path(args.repo), config_path)

"""Dashboard subcommands of the Agent Introspection CLI."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from agent_introspection import dashboard_service
from agent_introspection.cli_common import app_config


def add_dashboard_parser(commands: Any) -> None:
    dashboard = commands.add_parser(
        "dashboard", help="run the dashboard as a user service built from a checkout"
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


def dashboard_command(args: argparse.Namespace) -> dict[str, Any]:
    if args.dashboard_command == "status":
        return dashboard_service.status()
    if args.dashboard_command == "remove":
        return dashboard_service.remove()
    config_path = Path(args.config).expanduser().resolve() if args.config else None
    return dashboard_service.install(app_config(args), Path(args.repo), config_path)

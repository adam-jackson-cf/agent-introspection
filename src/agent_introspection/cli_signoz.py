"""SigNoz subcommands of the Agent Introspection CLI."""

from __future__ import annotations

import argparse
from typing import Any

from agent_introspection import signoz_detect
from agent_introspection.cli_common import Handler

_HANDLERS: dict[str, Handler] = {
    "detect": lambda _args: signoz_detect.detect(),
}


def add_signoz_parser(commands: Any) -> None:
    signoz = commands.add_parser(
        "signoz", help="inspect the SigNoz on this machine; never changes it"
    ).add_subparsers(dest="signoz_command", required=True)
    signoz.add_parser(
        "detect",
        help="find SigNoz and ClickHouse containers and local ports 4317, 4318, 8123, 8080",
    )


def signoz_command(args: argparse.Namespace) -> dict[str, Any]:
    return _HANDLERS[args.signoz_command](args)

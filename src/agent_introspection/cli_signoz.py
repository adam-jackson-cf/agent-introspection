"""SigNoz subcommands of the Agent Introspection CLI."""

from __future__ import annotations

import argparse
from typing import Any

from agent_introspection import signoz_detect
from agent_introspection.cli_common import Handler

_HANDLERS: dict[str, Handler] = {
    "detect": lambda args: signoz_detect.detect(args.clickhouse_url, args.otlp_endpoint),
}


def add_signoz_parser(commands: Any) -> None:
    signoz = commands.add_parser(
        "signoz", help="inspect the SigNoz on this machine; never changes it"
    ).add_subparsers(dest="signoz_command", required=True)
    detect = signoz.add_parser(
        "detect",
        help="find SigNoz and ClickHouse containers and local ports 4317, 4318, 8123, 8080",
    )
    detect.add_argument(
        "--clickhouse-url", help="check a ClickHouse HTTP interface here instead of 127.0.0.1:8123"
    )
    detect.add_argument("--otlp-endpoint", help="check an OTLP/HTTP collector endpoint here")


def signoz_command(args: argparse.Namespace) -> dict[str, Any]:
    return _HANDLERS[args.signoz_command](args)

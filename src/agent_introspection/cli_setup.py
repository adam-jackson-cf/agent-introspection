"""The ``setup`` command of the Agent Introspection CLI."""

from __future__ import annotations

import argparse
import shlex
from pathlib import Path
from typing import Any

from agent_introspection import setup_flow


def _argv(text: str) -> tuple[str, ...]:
    """Split a command given as one shell-quoted string into its arguments."""
    arguments = tuple(shlex.split(text))
    if not arguments:
        raise argparse.ArgumentTypeError("a command needs at least one argument")
    return arguments


def add_setup_parser(commands: Any) -> None:
    parser = commands.add_parser(
        "setup",
        help="install Agent Introspection on this machine in one run (--dry-run first)",
        description="Write the config, check SigNoz, install and backfill the facts, "
        "configure each enabled harness (export settings and activity hooks), schedule "
        "the sync, start the dashboard service, and run doctor, stopping at the first "
        "failing step. Every step is idempotent, so a rerun is a reinstall. Options set "
        "config keys; anything not given keeps its default. Never installs SigNoz.",
    )
    parser.add_argument("--dry-run", action="store_true", help="report each step, change nothing")
    parser.add_argument("--clickhouse-url", help="[signoz] clickhouse_url (default 127.0.0.1:8123)")
    parser.add_argument("--clickhouse-user", help="[signoz] clickhouse_user (default `default`)")
    parser.add_argument(
        "--clickhouse-password-command",
        type=_argv,
        help="[signoz] clickhouse_password_command, as one quoted command that prints it",
    )
    parser.add_argument("--otlp-endpoint", help="[signoz] otlp_endpoint (default 127.0.0.1:4318)")
    parser.add_argument(
        "--harness",
        action="append",
        default=None,
        metavar="KEY",
        help="a harness to enable, repeatable (default: claude-code and codex; omp is oh-my-pi)",
    )
    parser.add_argument(
        "--prompt-export",
        action="store_true",
        help="also export prompt text for labels (SigNoz keeps it for its retention)",
    )
    parser.add_argument(
        "--openrouter-key-command",
        type=_argv,
        help="[labels] openrouter_key_command, as one quoted command that prints the key",
    )
    parser.add_argument(
        "--backfill-days", type=int, default=setup_flow.DEFAULT_BACKFILL_DAYS, metavar="N"
    )
    parser.add_argument(
        "--no-dashboard", action="store_true", help="do not install the dashboard service"
    )
    parser.add_argument(
        "--repo", default=".", help="the checkout the dashboard is built from (default: here)"
    )


def setup_command(args: argparse.Namespace) -> dict[str, Any]:
    options = setup_flow.SetupOptions(
        config_path=Path(args.config).expanduser().resolve() if args.config else None,
        clickhouse_url=args.clickhouse_url,
        clickhouse_user=args.clickhouse_user,
        clickhouse_password_command=args.clickhouse_password_command,
        otlp_endpoint=args.otlp_endpoint,
        harnesses=tuple(args.harness) if args.harness else None,
        prompt_export=args.prompt_export,
        openrouter_key_command=args.openrouter_key_command,
        backfill_days=args.backfill_days,
        dashboard=not args.no_dashboard,
        repo=Path(args.repo),
        dry_run=args.dry_run,
    )
    return setup_flow.run(options)

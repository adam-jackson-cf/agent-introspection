"""Harness subcommands of the Agent Introspection CLI."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from agent_introspection import harness_config
from agent_introspection.cli_common import app_config
from agent_introspection.doctor_harness import VERSION_TIMEOUT_SECONDS


def add_harness_parser(commands: Any) -> None:
    harness = commands.add_parser(
        "harness", help="list, configure, and preview producer harnesses"
    ).add_subparsers(dest="harness_command", required=True)
    listing = harness.add_parser(
        "list",
        help="every harness profile, whether it is enabled, its binary, and the installed "
        "version (read-only)",
        description="List every harness profile, whether this machine enables it, its "
        "binary on PATH, and the version that binary prints. Each found binary is run "
        f"with its profile's version arguments, for at most {VERSION_TIMEOUT_SECONDS} s.",
    )
    listing.add_argument(
        "--no-version", action="store_true", help="run no binary; the version is not shown"
    )
    configure = harness.add_parser(
        "configure",
        help="write a registered harness's export settings and install its activity hooks",
        description="Write a registered harness's export settings into its own config "
        "files, pointing it at [signoz] otlp_endpoint (on an additional machine, at the "
        "host's collector, [machine] export_endpoint), then install its activity hooks "
        "(Claude Code settings hooks, the omp plugin) from this package. Only the "
        "profile's managed keys and this tool's own hook entries change; each changed "
        "file is first backed up beside it, and a second run changes nothing. A TOML "
        "table that cannot be edited line by line (sub-tables, dotted keys, or a value "
        "across lines) is rewritten as one table of inline values: its comments move "
        "above the key each described, and the file's notes in the output say so.",
    )
    configure.add_argument("key", help="a profile key, registry harness key, or service name")
    configure.add_argument(
        "--dry-run", action="store_true", help="report the changes, write nothing"
    )
    configure.add_argument(
        "--prompt-export",
        action="store_true",
        help="also write the opt-in prompt-export settings (prompt text for labels)",
    )
    configure.add_argument(
        "--force",
        action="store_true",
        help="rewrite a setting that already reaches the collector over the other transport",
    )
    configure.add_argument(
        "--root",
        action="extend",
        nargs="+",
        default=[],
        metavar="PATH",
        help="an extra config directory, such as another Codex home",
    )
    remove_hooks = harness.add_parser(
        "remove-hooks",
        help="take a harness's activity hooks out, keeping its export settings",
    )
    remove_hooks.add_argument("key", help="a profile key, registry harness key, or service name")
    remove_hooks.add_argument(
        "--dry-run", action="store_true", help="report the changes, write nothing"
    )
    remove_hooks.add_argument(
        "--root",
        action="extend",
        nargs="+",
        default=[],
        metavar="PATH",
        help="an extra config directory `harness configure --root` wrote hooks into",
    )
    impact = harness.add_parser(
        "impact", help="preview the signals enabling a producer would hide (read-only)"
    )
    impact.add_argument(
        "profile", help="a profile key, registry harness key, service name, or profile file"
    )


def harness_command(args: argparse.Namespace) -> dict[str, Any]:
    config = app_config(args)
    if args.harness_command == "list":
        return harness_config.harness_list(config.harnesses_enabled, versions=not args.no_version)
    if args.harness_command == "impact":
        return harness_config.harness_impact(args.profile, config.harnesses_enabled)
    if args.harness_command == "remove-hooks":
        return harness_config.remove_hooks(args.key, dry_run=args.dry_run, roots=args.root)
    options = harness_config.ConfigureOptions(
        dry_run=args.dry_run,
        prompt_export=args.prompt_export,
        force=args.force,
        roots=tuple(args.root),
        config_path=Path(args.config) if args.config else None,
    )
    return harness_config.configure(args.key, config.collector_endpoint, options)

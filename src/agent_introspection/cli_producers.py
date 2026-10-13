"""Producers subcommands of the Agent Introspection CLI.

``discover``, ``profile``, and ``fingerprint`` only read SigNoz. ``profile --write``
writes the draft profile to the given path, and ``fingerprint --save`` writes each
fingerprint under ``producers.FINGERPRINT_DIR``; nothing else is written.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent_introspection import facts, hooks, producers
from agent_introspection.cli_common import app_config, diagnostic
from agent_introspection.config import AppConfig


def _days(text: str) -> int:
    days = int(text)
    if days < 1:
        raise argparse.ArgumentTypeError("days must be at least 1")
    return days


def _add_days(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--days",
        type=_days,
        default=producers.DEFAULT_DAYS,
        help=f"the window to read, in days back from now (default {producers.DEFAULT_DAYS})",
    )


def add_producers_parser(commands: Any) -> None:
    producers_parser = commands.add_parser(
        "producers", help="identify and explore the services that send telemetry"
    ).add_subparsers(dest="producers_command", required=True)
    discover = producers_parser.add_parser(
        "discover", help="list every service in SigNoz over the window, classed (read-only)"
    )
    _add_days(discover)
    profile = producers_parser.add_parser(
        "profile",
        help="list one service's span, event, and key names with counts, and draft a "
        "candidate profile (read-only unless --write)",
    )
    profile.add_argument("service", help="the service.name to explore")
    _add_days(profile)
    profile.add_argument("--write", metavar="PATH", help="write the draft profile as TOML")
    profile.add_argument(
        "--force", action="store_true", help="let --write replace an existing file"
    )
    fingerprint = producers_parser.add_parser(
        "fingerprint",
        help="record each service's span, event, and key names with counts "
        "(read-only unless --save)",
    )
    fingerprint.add_argument(
        "services", nargs="*", metavar="service", help="default: the enabled registry harnesses"
    )
    _add_days(fingerprint)
    fingerprint.add_argument(
        "--save",
        action="store_true",
        help="write each fingerprint to ~/.local/share/agent-introspection/fingerprints/",
    )


def _profile(
    args: argparse.Namespace, run: facts.SqlRunner, registry: facts.Registry, config: AppConfig
) -> dict[str, Any]:
    result = producers.profile(
        run, args.service, args.days, registry, enabled=config.harnesses_enabled
    )
    for note in producers.truncation_notes(result):
        diagnostic(note)
    result["written"] = None
    if args.write is not None:
        producers.write_draft(result["draft"], Path(args.write), force=args.force)
        result["written"] = args.write
    return result


def _fingerprint_services(
    args: argparse.Namespace, registry: facts.Registry, config: AppConfig
) -> list[str]:
    services: list[str] = args.services or list(
        facts.enabled_harnesses(registry, config.harnesses_enabled)
    )
    return services


def _fingerprint(
    args: argparse.Namespace, run: facts.SqlRunner, registry: facts.Registry, config: AppConfig
) -> dict[str, Any]:
    services = _fingerprint_services(args, registry, config)
    records = producers.fingerprint(run, services, args.days)
    for service, record in records.items():
        for kind, dropped in record["truncated"].items():
            diagnostic(
                f"{service} {kind}: {dropped} names past the limit of "
                f"{producers.FINGERPRINT_LIMIT} dropped"
            )
    saved = (
        [
            producers.save_fingerprint(record, producers.FINGERPRINT_DIR)
            for record in records.values()
        ]
        if args.save
        else []
    )
    return {
        "days": args.days,
        "fingerprints": records,
        "saved": [hooks.redact_home(str(path)) for path in saved],
    }


def producers_command(args: argparse.Namespace) -> dict[str, Any]:
    config = app_config(args)
    registry = facts.load_registry()
    if args.producers_command == "profile" and args.write is not None:
        # Refuse a bad --write path before running any query.
        producers.draft_target(Path(args.write), force=args.force)
    if args.producers_command == "fingerprint" and args.save:
        # Refuse a service that cannot name a file before running any query, so a save
        # is never partial.
        for service in _fingerprint_services(args, registry, config):
            producers.fingerprint_path(producers.FINGERPRINT_DIR, service)
    run = facts.runner(config)
    handlers: dict[str, Callable[[], dict[str, Any]]] = {
        "discover": lambda: producers.discover(run, registry, config.harnesses_enabled, args.days),
        "profile": lambda: _profile(args, run, registry, config),
        "fingerprint": lambda: _fingerprint(args, run, registry, config),
    }
    return handlers[args.producers_command]()

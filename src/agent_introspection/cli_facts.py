"""Facts subcommands of the Agent Introspection CLI."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent_introspection import (
    classify,
    evaluation,
    facts,
    findings,
    inbox,
    preflight,
    schedule,
    sessions,
)
from agent_introspection.cli_common import app_config, diagnostic, open_store
from agent_introspection.config import AppConfig, load_config


def _schedule_command(args: argparse.Namespace) -> dict[str, Any]:
    if args.schedule_command == "install":
        # Validate now, and resolve the path so the job reads the same file later.
        config = Path(args.config).expanduser().resolve() if args.config else None
        load_config(config)
        return schedule.schedule_install(config)
    handlers: dict[str, Callable[[], dict[str, Any]]] = {
        "remove": schedule.schedule_remove,
        "status": schedule.schedule_status,
    }
    return handlers[args.schedule_command]()


def facts_command(args: argparse.Namespace) -> dict[str, Any]:
    if args.facts_command == "schedule":
        return _schedule_command(args)
    config = app_config(args)
    run = facts.runner(config)
    handlers: dict[str, Callable[[], dict[str, Any]]] = {
        "preflight": lambda: preflight.preflight(run, config),
        "install": lambda: _install(args, config, run),
        "backfill": lambda: facts.backfill(run, days=args.days),
        "sessions": lambda: _sessions(config, run, rescan=args.rescan),
        "findings": lambda: _findings(args, run),
        "sync": lambda: _sync(args, run),
        "classify": lambda: _labels(run, days=args.days, limit=args.limit),
        "status": lambda: facts.status(run),
    }
    return handlers[args.facts_command]()


def _install(args: argparse.Namespace, config: AppConfig, run: facts.SqlRunner) -> dict[str, Any]:
    result = facts.install(run, config.harnesses_enabled)
    # The dashboard reads the workflow store, so install creates and migrates it too.
    open_store(args).close()
    return result


def _findings(args: argparse.Namespace, run: facts.SqlRunner) -> dict[str, Any]:
    connection = open_store(args)
    try:
        return findings.refresh(run, connection)
    finally:
        connection.close()


def _labels(run: facts.SqlRunner, *, days: int = 2, limit: int = 60) -> dict[str, Any]:
    """Label exported prompts with Jev; Jev being unavailable never fails the sync."""
    try:
        return classify.run(run, days=days, limit=limit)
    except classify.JevUnavailableError as exc:
        diagnostic(f"prompt labels skipped: {exc}")
        return {"status": "skipped", "reason": str(exc)}


def _sessions(config: AppConfig, run: facts.SqlRunner, *, rescan: bool = False) -> dict[str, Any]:
    """Attribute new sessions from the harnesses' session stores to Git projects."""
    stores = sessions.load_stores(config.session_roots)
    return sessions.resolve(run, stores=stores, rescan=rescan)


def _sync(args: argparse.Namespace, run: facts.SqlRunner) -> dict[str, Any]:
    """Load hook events and sessions, label prompts, promote findings, evaluate proposals."""
    hooks = inbox.sync(run)
    attributed = _sessions(app_config(args), run)
    labels = _labels(run)
    connection = open_store(args)
    try:
        refreshed = findings.refresh(run, connection)
        evaluated = evaluation.evaluate_due(run, connection)
    finally:
        connection.close()
    return {
        "hooks": hooks,
        "sessions": attributed,
        "labels": labels,
        "findings": refreshed,
        "evaluations": evaluated,
    }


def add_facts_parser(commands: Any) -> None:
    facts_parser = commands.add_parser("facts").add_subparsers(dest="facts_command", required=True)
    facts_parser.add_parser("preflight", help="read-only checks before facts install")
    facts_parser.add_parser("install")
    backfill = facts_parser.add_parser("backfill")
    backfill.add_argument("--days", type=int, default=90)
    facts_parser.add_parser("status")
    sessions_parser = facts_parser.add_parser(
        "sessions", help="attribute sessions from the harnesses' session stores now"
    )
    sessions_parser.add_argument(
        "--rescan", action="store_true", help="re-read every session file, not only new ones"
    )
    facts_parser.add_parser("findings")
    facts_parser.add_parser("sync")
    classify_parser = facts_parser.add_parser("classify", help="label exported prompts now")
    classify_parser.add_argument("--days", type=int, default=2)
    classify_parser.add_argument("--limit", type=int, default=60)
    schedule = facts_parser.add_parser("schedule").add_subparsers(
        dest="schedule_command", required=True
    )
    for action in ("install", "remove", "status"):
        schedule.add_parser(action)

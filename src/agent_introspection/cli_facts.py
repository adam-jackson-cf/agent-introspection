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
    preflight,
    schedule,
    sessions,
    spool,
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
    if args.facts_command == "preflight":
        # Preflight always prints its report: setup errors become failing checks.
        return preflight.report(lambda: app_config(args), facts.runner)
    config = app_config(args)
    run = facts.runner(config)
    handlers: dict[str, Callable[[], dict[str, Any]]] = {
        "verify": lambda: (
            facts.compare(run) if args.compare else facts.verify(run, config.harnesses_enabled)
        ),
        "install": lambda: _install(args, config, run),
        "refresh": lambda: facts.refresh_named(run, args.refresher),
        "backfill": lambda: facts.backfill(run, days=args.days),
        "sessions": lambda: _sessions(config, rescan=args.rescan),
        "findings": lambda: _findings(args, run),
        "sync": lambda: _sync(args, run),
        "classify": lambda: _labels(run, config, days=args.days, limit=args.limit),
        "status": lambda: facts.status(run),
    }
    return handlers[args.facts_command]()


def _install(args: argparse.Namespace, config: AppConfig, run: facts.SqlRunner) -> dict[str, Any]:
    try:
        result = facts.install(run, config.harnesses_enabled)
    except Exception:
        # The dashboard reads the workflow store, so install creates and migrates it
        # too, even when a refresh failed after the ClickHouse store was created.
        # A store error here must not hide the install failure that is propagating.
        try:
            open_store(args).close()
        except Exception as store_error:
            diagnostic(f"workflow store setup also failed: {store_error}")
        raise
    open_store(args).close()
    return result


def _findings(args: argparse.Namespace, run: facts.SqlRunner) -> dict[str, Any]:
    connection = open_store(args)
    try:
        return findings.refresh(run, connection)
    finally:
        connection.close()


def _labels(
    run: facts.SqlRunner, config: AppConfig, *, days: int = 2, limit: int = 60
) -> dict[str, Any]:
    """Label exported prompts with Jev; Jev being unavailable never fails the sync."""
    command = config.labels_openrouter_key_command
    try:
        return classify.run(run, days=days, limit=limit, key=lambda: classify.api_key(command))
    except classify.JevUnavailableError as exc:
        diagnostic(f"prompt labels skipped: {exc}")
        return {"status": "skipped", "reason": str(exc)}


def _sessions(config: AppConfig, *, rescan: bool = False) -> dict[str, Any]:
    """Attribute new sessions from the harnesses' session stores into the spool."""
    stores = sessions.load_stores(config.session_roots)
    return sessions.resolve(stores=stores, rescan=rescan)


_LOADERS = ("load_hook_events", "load_session_projects")


def _load_spooled(run: facts.SqlRunner) -> None:
    """Run the loaders now, so records the drain just delivered are in their tables."""
    for name in _LOADERS:
        run(f"SYSTEM REFRESH VIEW {facts.DATABASE}.{name}")
        run(f"SYSTEM WAIT VIEW {facts.DATABASE}.{name}")


def _sync(args: argparse.Namespace, run: facts.SqlRunner) -> dict[str, Any]:
    """Ship the spool, load it, label prompts, promote findings, evaluate proposals."""
    config = app_config(args)
    shipped = spool.sync(config)
    _load_spooled(run)
    labels = _labels(run, config)
    connection = open_store(args)
    try:
        refreshed = findings.refresh(run, connection)
        evaluated = evaluation.evaluate_due(run, connection)
    finally:
        connection.close()
    return {
        "spool": shipped,
        "labels": labels,
        "findings": refreshed,
        "evaluations": evaluated,
    }


def add_facts_parser(commands: Any) -> None:
    facts_parser = commands.add_parser("facts").add_subparsers(dest="facts_command", required=True)
    facts_parser.add_parser("preflight", help="read-only checks before facts install")
    verify = facts_parser.add_parser(
        "verify", help="build the store in a scratch database to check every statement"
    )
    verify.add_argument(
        "--compare",
        action="store_true",
        help="rebuild the live snapshots and check each matches a full read of its view",
    )
    facts_parser.add_parser("install")
    refresh = facts_parser.add_parser(
        "refresh", help="refresh and wait for one loader or snapshot refresher now"
    )
    refresh.add_argument(
        "refresher", help="a name from `facts status`, e.g. refresh_tool_calls_recent"
    )
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
    sync = facts_parser.add_parser("sync")
    sync.add_argument(
        "--log",
        action="store_true",
        help="log the summary and errors to the rotated, timestamped sync log (scheduled job)",
    )
    classify_parser = facts_parser.add_parser("classify", help="label exported prompts now")
    classify_parser.add_argument("--days", type=int, default=2)
    classify_parser.add_argument("--limit", type=int, default=60)
    schedule = facts_parser.add_parser("schedule").add_subparsers(
        dest="schedule_command", required=True
    )
    for action in ("install", "remove", "status"):
        schedule.add_parser(action)


def add_spool_parser(commands: Any) -> None:
    spool_parser = commands.add_parser(
        "spool", help="ship this machine's records to the OTLP route"
    ).add_subparsers(dest="spool_command", required=True)
    sync = spool_parser.add_parser(
        "sync", help="attribute new sessions, record spool status, and send the spool"
    )
    sync.add_argument("--rescan", action="store_true", help="re-read every session file")
    spool_parser.add_parser("status", help="count pending and rejected spool files (read-only)")


def spool_command(args: argparse.Namespace) -> dict[str, Any]:
    if args.spool_command == "status":
        return {
            "pending": spool.backlog(),
            "rejected": spool.rejected_count(),
            **spool.read_state(),
        }
    return spool.sync(app_config(args), rescan=args.rescan)

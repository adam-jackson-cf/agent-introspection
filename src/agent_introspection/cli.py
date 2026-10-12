"""Structured command-line interface for Agent Introspection."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from typing import Any, NoReturn

from agent_introspection import facts
from agent_introspection.cli_common import diagnostic
from agent_introspection.cli_facts import add_facts_parser, facts_command
from agent_introspection.cli_proposals import (
    PROPOSAL_HANDLERS,
    add_candidates_parser,
    add_proposal_parser,
    candidates_export,
)
from agent_introspection.config import ConfigurationError
from agent_introspection.schedule import log_line

EXIT_CONFIG = 10
EXIT_FACTS = 30
EXIT_DATABASE = 40
EXIT_VALIDATION = 50
EXIT_CONFLICT = 60
EXIT_INTERNAL = 70


def _emit(value: object, log: bool = False) -> None:
    line = json.dumps(value, sort_keys=True, separators=(",", ":"))
    if log:
        log_line(line)
    else:
        print(line)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-introspection")
    parser.add_argument("--config", default=os.environ.get("AGENT_INTROSPECTION_CONFIG"))
    commands = parser.add_subparsers(dest="command", required=True)
    add_facts_parser(commands)
    add_candidates_parser(commands)
    add_proposal_parser(commands)
    hook = commands.add_parser("hook", help="normalize one activity-hook envelope from stdin")
    hook.add_argument("producer", help="claude-code, codex, or omp")
    hook.add_argument("event", help="the native hook event name")
    return parser


def _dispatch(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "facts":
        return facts_command(args)
    if args.command == "candidates":
        return candidates_export(args)
    return PROPOSAL_HANDLERS[args.proposal_command](args)


def _fail(code: int, exc: BaseException, log: bool = False) -> NoReturn:
    message = f"{type(exc).__name__}: {exc}"
    if log:
        log_line(message)
    else:
        diagnostic(message)
    raise SystemExit(code)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "hook":
        from agent_introspection import hooks

        # Hooks never fail the harness: failures go to the hook log, never stdout.
        return hooks.main(args.producer, args.event)
    log = bool(getattr(args, "log", False))
    try:
        result = _dispatch(args)
    except ConfigurationError as exc:
        _fail(EXIT_CONFIG, exc, log)
    except facts.FactsError as exc:
        _fail(EXIT_FACTS, exc, log)
    except sqlite3.Error as exc:
        _fail(EXIT_DATABASE, exc, log)
    except (ValueError, KeyError, PermissionError) as exc:
        _fail(EXIT_VALIDATION, exc, log)
    except RuntimeError as exc:
        _fail(EXIT_CONFLICT, exc, log)
    except Exception as exc:
        _fail(EXIT_INTERNAL, exc, log)
    _emit(result, log)
    # A failed preflight or snapshot comparison still prints every result, then exits
    # non-zero.
    checked = getattr(args, "facts_command", None) in ("preflight", "verify")
    failed = checked and not result["passed"]
    return EXIT_FACTS if failed else 0

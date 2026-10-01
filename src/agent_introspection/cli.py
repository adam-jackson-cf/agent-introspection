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

EXIT_CONFIG = 10
EXIT_FACTS = 30
EXIT_DATABASE = 40
EXIT_VALIDATION = 50
EXIT_CONFLICT = 60
EXIT_INTERNAL = 70


def _emit(value: object) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))


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


def _fail(code: int, exc: BaseException) -> NoReturn:
    diagnostic(f"{type(exc).__name__}: {exc}")
    raise SystemExit(code)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "hook":
        from agent_introspection import hooks

        # Hooks never fail the harness: failures go to the hook log, never stdout.
        return hooks.main(args.producer, args.event)
    try:
        result = _dispatch(args)
    except ConfigurationError as exc:
        _fail(EXIT_CONFIG, exc)
    except facts.FactsError as exc:
        _fail(EXIT_FACTS, exc)
    except sqlite3.Error as exc:
        _fail(EXIT_DATABASE, exc)
    except (ValueError, KeyError, PermissionError) as exc:
        _fail(EXIT_VALIDATION, exc)
    except RuntimeError as exc:
        _fail(EXIT_CONFLICT, exc)
    except Exception as exc:
        _fail(EXIT_INTERNAL, exc)
    _emit(result)
    # A failed preflight still prints every check, then exits non-zero.
    failed = getattr(args, "facts_command", None) == "preflight" and not result["passed"]
    return EXIT_FACTS if failed else 0

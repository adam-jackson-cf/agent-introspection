"""Structured command-line interface for Agent Introspection."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn

from agent_introspection import facts, projects
from agent_introspection.config import AppConfig, ConfigurationError, load_config
from agent_introspection.proposals import (
    ProposalInput,
    ProposalState,
    TransitionProposalRequest,
    create_proposal,
    transition_proposal,
)
from agent_introspection.review import (
    create_review_session,
    import_model_output,
    validate_model_output,
)
from agent_introspection.workflow import connect_workflow

EXIT_CONFIG = 10
EXIT_FACTS = 30
EXIT_DATABASE = 40
EXIT_VALIDATION = 50
EXIT_CONFLICT = 60
EXIT_INTERNAL = 70

type Handler = Callable[[argparse.Namespace], dict[str, Any]]


def _emit(value: object) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _diagnostic(message: str) -> None:
    print(message, file=sys.stderr)


def _read_json(source: str) -> dict[str, Any]:
    try:
        text = sys.stdin.read() if source == "-" else Path(source).read_text()
        value = json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("input JSON is unreadable or invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("input JSON must contain an object")
    return value


def _config(args: argparse.Namespace) -> AppConfig:
    return load_config(Path(args.config) if args.config is not None else None)


def _open(args: argparse.Namespace) -> sqlite3.Connection:
    config = _config(args)
    return connect_workflow(config.database.path, busy_timeout_ms=config.database.busy_timeout_ms)


def _candidates_export(args: argparse.Namespace) -> dict[str, Any]:
    connection = _open(args)
    try:
        rows = connection.execute(
            """
            SELECT f.id, f.category, f.trend_state, f.fingerprint
            FROM findings f
            LEFT JOIN proposals p ON p.finding_id = f.id
            WHERE f.trend_state = 'actionable' AND p.id IS NULL
            ORDER BY f.last_seen_ns, f.id LIMIT 1
            """
        ).fetchall()
        if not rows:
            return {"status": "no_candidates"}
        candidates = [{"id": str(row[0]), "fields": list(row[1:])} for row in rows]
        envelope = create_review_session(
            connection,
            candidates=candidates,
            reserved_model_budget=args.reserved_model_budget,
            batch_id=args.batch_id,
        )
        return {"status": "exported", "review": envelope.as_dict()}
    finally:
        connection.close()


def _proposal_create(args: argparse.Namespace) -> dict[str, Any]:
    document = _read_json(args.input_json)
    provenance = document.pop("provenance", None)
    if not isinstance(provenance, dict):
        raise ValueError("proposal creation requires provenance")
    connection = _open(args)
    try:
        results = validate_model_output(connection, document, provenance=provenance)
        proposal_inputs: list[ProposalInput] = []
        for result in results:
            payload = result.get("proposal")
            if not isinstance(payload, dict):
                raise ValueError("proposal result requires a proposal object")
            proposal_input = ProposalInput(**payload)
            finding = connection.execute(
                "SELECT trend_state FROM findings WHERE id = ?", (proposal_input.finding_id,)
            ).fetchone()
            if finding is None or finding[0] != "actionable":
                raise ValueError("only actionable findings can produce proposals")
            proposal_inputs.append(proposal_input)
        import_model_output(connection, document, provenance=provenance)
        proposal_ids = [create_proposal(connection, value) for value in proposal_inputs]
        return {"status": "created", "proposal_ids": proposal_ids}
    finally:
        connection.close()


def _proposal_list(args: argparse.Namespace) -> dict[str, Any]:
    connection = _open(args)
    try:
        rows = connection.execute(
            "SELECT id, finding_id, state, created_at, updated_at "
            "FROM proposals ORDER BY created_at"
        ).fetchall()
        keys = ("id", "finding_id", "state", "created_at", "updated_at")
        return {"proposals": [dict(zip(keys, row, strict=True)) for row in rows]}
    finally:
        connection.close()


def _proposal_show(args: argparse.Namespace) -> dict[str, Any]:
    connection = _open(args)
    try:
        row = connection.execute(
            "SELECT id, finding_id, state, payload_json, entity_version "
            "FROM proposals WHERE id = ?",
            (args.proposal_id,),
        ).fetchone()
        if row is None:
            raise KeyError(args.proposal_id)
        events = connection.execute(
            """
            SELECT sequence, event_type, payload_json, created_at
            FROM proposal_events WHERE proposal_id = ? ORDER BY sequence
            """,
            (args.proposal_id,),
        ).fetchall()
        return {
            "id": row[0],
            "finding_id": row[1],
            "state": row[2],
            "proposal": json.loads(row[3]),
            "entity_version": row[4],
            "events": [
                {
                    "sequence": event[0],
                    "event_type": event[1],
                    "payload": json.loads(event[2]),
                    "created_at": event[3],
                }
                for event in events
            ],
        }
    finally:
        connection.close()


def _proposal_decide(args: argparse.Namespace) -> dict[str, Any]:
    target = ProposalState.APPROVED if args.decision == "approve" else ProposalState.REJECTED
    connection = _open(args)
    try:
        transition_proposal(
            connection,
            TransitionProposalRequest(
                proposal_id=args.proposal_id,
                target_state=target,
                actor=args.actor,
                evidence={"decision": args.decision, "reason": args.reason},
            ),
        )
        return {"status": target, "proposal_id": args.proposal_id}
    finally:
        connection.close()


def _proposal_mark_applied(args: argparse.Namespace) -> dict[str, Any]:
    evidence = _read_json(args.input_json)
    if not evidence.get("validation"):
        raise ValueError("mark-applied requires validation evidence")
    connection = _open(args)
    try:
        state = connection.execute(
            "SELECT state FROM proposals WHERE id = ?", (args.proposal_id,)
        ).fetchone()
        if state is None:
            raise KeyError(args.proposal_id)
        if state[0] == ProposalState.APPROVED:
            transition_proposal(
                connection,
                TransitionProposalRequest(
                    proposal_id=args.proposal_id,
                    target_state=ProposalState.APPLYING,
                    actor=args.actor,
                    evidence={"request": "mark-applied"},
                    explicit_application_request=True,
                ),
            )
        transition_proposal(
            connection,
            TransitionProposalRequest(
                proposal_id=args.proposal_id,
                target_state=ProposalState.APPLIED,
                actor=args.actor,
                evidence=evidence,
            ),
        )
        return {"status": "applied", "proposal_id": args.proposal_id}
    finally:
        connection.close()


def _facts_command(args: argparse.Namespace) -> dict[str, Any]:
    if args.facts_command == "schedule":
        return {
            "install": projects.schedule_install,
            "remove": projects.schedule_remove,
            "status": projects.schedule_status,
        }[args.schedule_command]()
    run = facts.docker_runner(_config(args))
    if args.facts_command == "install":
        return facts.install(run)
    if args.facts_command == "backfill":
        return facts.backfill(run, days=args.days)
    if args.facts_command == "sync-projects":
        ledger = Path(args.ledger).expanduser() if args.ledger is not None else None
        return projects.sync(run, ledger=ledger)
    return facts.status(run)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-introspection")
    parser.add_argument("--config", default=os.environ.get("AGENT_INTROSPECTION_CONFIG"))
    commands = parser.add_subparsers(dest="command", required=True)

    facts_parser = commands.add_parser("facts").add_subparsers(dest="facts_command", required=True)
    facts_parser.add_parser("install")
    backfill = facts_parser.add_parser("backfill")
    backfill.add_argument("--days", type=int, default=90)
    facts_parser.add_parser("status")
    sync = facts_parser.add_parser("sync-projects")
    sync.add_argument("--ledger", help="import session-context history from a retired ledger")
    schedule = facts_parser.add_parser("schedule").add_subparsers(
        dest="schedule_command", required=True
    )
    for action in ("install", "remove", "status"):
        schedule.add_parser(action)

    candidates = commands.add_parser("candidates").add_subparsers(
        dest="candidates_command", required=True
    )
    export = candidates.add_parser("export")
    export.add_argument("--batch-id")
    export.add_argument("--reserved-model-budget", type=int, required=True)

    proposal = commands.add_parser("proposal").add_subparsers(
        dest="proposal_command", required=True
    )
    create = proposal.add_parser("create")
    create.add_argument("--input-json", required=True)
    proposal.add_parser("list")
    show = proposal.add_parser("show")
    show.add_argument("proposal_id")
    decide = proposal.add_parser("decide")
    decide.add_argument("proposal_id")
    decide.add_argument("decision", choices=("approve", "reject"))
    decide.add_argument("--actor", required=True)
    decide.add_argument("--reason", required=True)
    applied = proposal.add_parser("mark-applied")
    applied.add_argument("proposal_id")
    applied.add_argument("--actor", required=True)
    applied.add_argument("--input-json", required=True)
    return parser


_PROPOSAL_HANDLERS: dict[str, Handler] = {
    "create": _proposal_create,
    "list": _proposal_list,
    "show": _proposal_show,
    "decide": _proposal_decide,
    "mark-applied": _proposal_mark_applied,
}


def _dispatch(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "facts":
        return _facts_command(args)
    if args.command == "candidates":
        return _candidates_export(args)
    return _PROPOSAL_HANDLERS[args.proposal_command](args)


def _fail(code: int, exc: BaseException) -> NoReturn:
    _diagnostic(f"{type(exc).__name__}: {exc}")
    raise SystemExit(code)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
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
    return 0

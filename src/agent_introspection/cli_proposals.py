"""Proposal and candidate subcommands of the Agent Introspection CLI."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from typing import Any

from agent_introspection import candidates as candidate_export, drafting, evaluation, facts
from agent_introspection.cli_common import Handler, app_config, open_store, read_json
from agent_introspection.proposals import (
    ProposalState,
    TransitionProposalRequest,
    immediate_transaction,
    require_successful_validation,
    transition_proposal,
)


def candidates_export(args: argparse.Namespace) -> dict[str, Any]:
    run = facts.runner(app_config(args))
    connection = open_store(args)
    try:
        return candidate_export.export(
            connection,
            run,
            reserved_model_budget=args.reserved_model_budget,
            batch_id=args.batch_id,
        )
    finally:
        connection.close()


def _create(args: argparse.Namespace) -> dict[str, Any]:
    document = read_json(args.input_json)
    provenance = document.pop("provenance", None)
    if not isinstance(provenance, dict):
        raise ValueError("proposal creation requires provenance")
    connection = open_store(args)
    try:
        proposal_ids = drafting.import_proposals(connection, document, provenance)
        return {"status": "created", "proposal_ids": proposal_ids}
    finally:
        connection.close()


def _draft(args: argparse.Namespace) -> dict[str, Any]:
    run = facts.runner(app_config(args))
    connection = open_store(args)
    try:
        request = drafting.DraftRequest(args.reserved_model_budget, args.batch_id, args.dry_run)
        return drafting.draft(connection, run, request)
    finally:
        connection.close()


def _evaluate(args: argparse.Namespace) -> dict[str, Any]:
    now = datetime.fromisoformat(args.now) if args.now is not None else None
    if now is not None and now.tzinfo is None:
        raise ValueError("--now must include a UTC offset")
    run = facts.runner(app_config(args))
    connection = open_store(args)
    try:
        return evaluation.evaluate_due(run, connection, now)
    finally:
        connection.close()


def _list(args: argparse.Namespace) -> dict[str, Any]:
    connection = open_store(args)
    try:
        rows = connection.execute(
            "SELECT id, finding_id, state, created_at, updated_at "
            "FROM proposals ORDER BY created_at"
        ).fetchall()
        keys = ("id", "finding_id", "state", "created_at", "updated_at")
        return {"proposals": [dict(zip(keys, row, strict=True)) for row in rows]}
    finally:
        connection.close()


def _show(args: argparse.Namespace) -> dict[str, Any]:
    connection = open_store(args)
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


def _decide(args: argparse.Namespace) -> dict[str, Any]:
    target = ProposalState.APPROVED if args.decision == "approve" else ProposalState.REJECTED
    connection = open_store(args)
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


def _mark_applied(args: argparse.Namespace) -> dict[str, Any]:
    evidence = read_json(args.input_json)
    require_successful_validation(evidence)
    connection = open_store(args)
    try:
        # One transaction covers approved -> applying -> applied, so a failure or a
        # competing change never strands the proposal in applying.
        with immediate_transaction(connection):
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
                    outer_transaction=True,
                )
            transition_proposal(
                connection,
                TransitionProposalRequest(
                    proposal_id=args.proposal_id,
                    target_state=ProposalState.APPLIED,
                    actor=args.actor,
                    evidence=evidence,
                ),
                outer_transaction=True,
            )
        return {"status": "applied", "proposal_id": args.proposal_id}
    finally:
        connection.close()


PROPOSAL_HANDLERS: dict[str, Handler] = {
    "create": _create,
    "list": _list,
    "show": _show,
    "decide": _decide,
    "mark-applied": _mark_applied,
    "draft": _draft,
    "evaluate": _evaluate,
}


def add_candidates_parser(commands: Any) -> None:
    candidates = commands.add_parser("candidates").add_subparsers(
        dest="candidates_command", required=True
    )
    export = candidates.add_parser("export")
    export.add_argument("--batch-id")
    export.add_argument("--reserved-model-budget", type=int, required=True)


def add_proposal_parser(commands: Any) -> None:
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
    draft = proposal.add_parser("draft")
    draft.add_argument("--batch-id")
    draft.add_argument("--reserved-model-budget", type=int, required=True)
    draft.add_argument("--dry-run", action="store_true")
    evaluate = proposal.add_parser("evaluate")
    evaluate.add_argument("--now", help="ISO 8601 instant with offset (default: now)")

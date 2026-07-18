"""Approval-gated proposal state transitions."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from agent_introspection.telemetry import REVIEW_SCOPE, DerivedEvent, enqueue_event


class ProposalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLYING = "applying"
    APPLIED = "applied"
    IMPLEMENTATION_FAILED = "implementation_failed"


ALLOWED_TRANSITIONS: dict[ProposalState, frozenset[ProposalState]] = {
    ProposalState.PENDING: frozenset({ProposalState.APPROVED, ProposalState.REJECTED}),
    ProposalState.APPROVED: frozenset({ProposalState.APPLYING}),
    ProposalState.APPLYING: frozenset({ProposalState.APPLIED, ProposalState.IMPLEMENTATION_FAILED}),
    ProposalState.REJECTED: frozenset(),
    ProposalState.APPLIED: frozenset(),
    ProposalState.IMPLEMENTATION_FAILED: frozenset(),
}


@dataclass(frozen=True)
class ProposalInput:
    finding_id: str
    root_cause: str
    trend_window: str
    occurrence_count: int
    task_count: int
    day_count: int
    representative_evidence: list[str]
    membership_rationale: str
    intervention_type: str
    scope: str
    target: str
    intended_change: str
    established_tool_audit: list[dict[str, str]]
    rejected_alternatives: list[str]
    validation_criteria: list[str]
    rollback_criteria: list[str]
    predicted_success_metric: str
    create_skill_handoff: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        tiers = [entry.get("tier") for entry in self.established_tool_audit]
        expected = [
            "Established tools of a project first.",
            "New tools second.",
            "Bespoke scripts third.",
        ]
        if tiers != expected:
            raise ValueError("established-tool audit must evaluate the canonical tier order")
        available = 0
        for entry in self.established_tool_audit:
            can_enforce = entry.get("can_enforce")
            if not isinstance(can_enforce, bool):
                raise ValueError("each deterministic enforcement tier requires can_enforce")
            if can_enforce:
                available += 1
                if entry.get("reason_unavailable") is not None:
                    raise ValueError("an available enforcement tier cannot be unavailable")
            elif not entry.get("reason_unavailable"):
                raise ValueError("each unavailable enforcement tier requires a recorded reason")
        if available > 1:
            raise ValueError("a proposal can select only one deterministic enforcement tier")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def create_proposal(connection: sqlite3.Connection, proposal: ProposalInput) -> str:
    """Persist a pending proposal and its immutable creation event."""
    finding = connection.execute(
        "SELECT trend_state, detector_id FROM findings WHERE id = ?", (proposal.finding_id,)
    ).fetchone()
    if finding is None:
        raise KeyError(proposal.finding_id)
    if finding[0] != "actionable":
        raise ValueError("only actionable findings can produce proposals")
    proposal_id = str(uuid.uuid4())
    now = _now()
    payload = json.dumps(proposal.__dict__, sort_keys=True, separators=(",", ":"))
    with connection:
        connection.execute(
            """
            INSERT INTO proposals (
                id, finding_id, state, payload_json, created_at, updated_at, entity_version
            ) VALUES (?, ?, ?, ?, ?, ?, 1)
            """,
            (proposal_id, proposal.finding_id, ProposalState.PENDING, payload, now, now),
        )
        connection.execute(
            """
            INSERT INTO proposal_events (
                id, proposal_id, sequence, event_type, payload_json, created_at
            ) VALUES (?, ?, 1, 'created', ?, ?)
            """,
            (str(uuid.uuid4()), proposal_id, payload, now),
        )
    enqueue_event(
        connection,
        DerivedEvent(
            scope=REVIEW_SCOPE,
            entity_id=proposal_id,
            entity_version=1,
            event_sequence=1,
            event_name="introspection.proposal.state_changed",
            attributes={
                "proposal.state": "pending",
                "proposal.scope": proposal.scope,
                "intervention.type": proposal.intervention_type,
                "finding.id": proposal.finding_id,
                "detector.id": str(finding[1]),
            },
            timestamp_ns=int(datetime.fromisoformat(now).timestamp() * 1_000_000_000),
        ),
    )
    return proposal_id


def transition_proposal(
    connection: sqlite3.Connection,
    proposal_id: str,
    target_state: ProposalState,
    *,
    actor: str,
    evidence: dict[str, Any],
    explicit_application_request: bool = False,
) -> None:
    """Apply a valid transition while retaining an immutable event history."""
    row = connection.execute(
        """
        SELECT p.state, p.entity_version, p.finding_id, p.payload_json, f.detector_id
        FROM proposals p JOIN findings f ON f.id = p.finding_id WHERE p.id = ?
        """,
        (proposal_id,),
    ).fetchone()
    if row is None:
        raise KeyError(proposal_id)
    source_state = ProposalState(row[0])
    if target_state not in ALLOWED_TRANSITIONS[source_state]:
        raise ValueError(f"invalid proposal transition: {source_state} -> {target_state}")
    if target_state is ProposalState.APPLYING and not explicit_application_request:
        raise PermissionError("entering applying requires a separate explicit user request")
    if target_state is ProposalState.APPLIED and not evidence.get("validation"):
        raise ValueError("validation evidence is required before marking applied")
    version = int(row[1]) + 1
    event_payload = json.dumps(
        {"actor": actor, "evidence": evidence, "from": source_state, "to": target_state},
        sort_keys=True,
        separators=(",", ":"),
    )
    now = _now()
    with connection:
        sequence = connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM proposal_events WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchone()[0]
        connection.execute(
            "UPDATE proposals SET state = ?, updated_at = ?, entity_version = ? WHERE id = ?",
            (target_state, now, version, proposal_id),
        )
        connection.execute(
            """
            INSERT INTO proposal_events (
                id, proposal_id, sequence, event_type, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), proposal_id, sequence, target_state, event_payload, now),
        )
    proposal_payload = json.loads(row[3])
    enqueue_event(
        connection,
        DerivedEvent(
            scope=REVIEW_SCOPE,
            entity_id=proposal_id,
            entity_version=version,
            event_sequence=version,
            event_name="introspection.proposal.state_changed",
            attributes={
                "proposal.state": str(target_state),
                "proposal.scope": str(proposal_payload["scope"]),
                "intervention.type": str(proposal_payload["intervention_type"]),
                "finding.id": str(row[2]),
                "detector.id": str(row[4]),
            },
            timestamp_ns=int(datetime.fromisoformat(now).timestamp() * 1_000_000_000),
        ),
    )

import sqlite3
from dataclasses import replace

import pytest

from agent_introspection.proposals import (
    ProposalInput,
    ProposalState,
    create_proposal,
    transition_proposal,
)


def proposal_database(state: str = "actionable") -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE findings (
          id TEXT PRIMARY KEY, trend_state TEXT NOT NULL, detector_id TEXT NOT NULL
        );
        CREATE TABLE proposals (
          id TEXT PRIMARY KEY, finding_id TEXT, state TEXT, payload_json TEXT,
          created_at TEXT, updated_at TEXT, entity_version INTEGER
        );
        CREATE TABLE proposal_events (
          id TEXT PRIMARY KEY, proposal_id TEXT, sequence INTEGER, event_type TEXT,
          payload_json TEXT, created_at TEXT
        );
        CREATE TABLE otlp_outbox (
          event_id TEXT PRIMARY KEY, payload_json TEXT, status TEXT, attempt_count INTEGER,
          next_attempt_at TEXT, created_at TEXT, delivered_at TEXT
        );
        """
    )
    connection.execute("INSERT INTO findings VALUES ('finding-1', ?, 'tool_failure')", (state,))
    connection.commit()
    return connection


def proposal_input() -> ProposalInput:
    return ProposalInput(
        finding_id="finding-1",
        root_cause="Repeated unsafe workflow",
        trend_window="seven days",
        occurrence_count=3,
        task_count=2,
        day_count=2,
        representative_evidence=["observation-1"],
        membership_rationale="same deterministic fingerprint",
        intervention_type="established_tool",
        scope="project",
        target="quality command",
        intended_change="enforce the command before mutation",
        established_tool_audit=[
            {
                "tier": "Established tools of a project first.",
                "can_enforce": True,
                "reason_unavailable": None,
            },
            {
                "tier": "New tools second.",
                "can_enforce": False,
                "reason_unavailable": "established tool selected",
            },
            {
                "tier": "Bespoke scripts third.",
                "can_enforce": False,
                "reason_unavailable": "established tool selected",
            },
        ],
        rejected_alternatives=["new tool", "bespoke script"],
        validation_criteria=["quality command passes before mutation"],
        rollback_criteria=["restore prior tool configuration"],
        predicted_success_metric="zero bypass observations in seven days",
    )


def test_only_actionable_findings_can_create_one_pending_proposal() -> None:
    connection = proposal_database()
    proposal_id = create_proposal(connection, proposal_input())
    assert connection.execute(
        "SELECT state, entity_version FROM proposals WHERE id = ?", (proposal_id,)
    ).fetchone() == ("pending", 1)
    assert (
        connection.execute(
            "SELECT event_type FROM proposal_events WHERE proposal_id = ?", (proposal_id,)
        ).fetchone()[0]
        == "created"
    )
    with pytest.raises(ValueError, match="actionable"):
        create_proposal(proposal_database("isolated"), proposal_input())


def test_approval_is_a_decision_and_application_requires_separate_explicit_request() -> None:
    connection = proposal_database()
    proposal_id = create_proposal(connection, proposal_input())
    transition_proposal(
        connection,
        proposal_id,
        ProposalState.APPROVED,
        actor="user",
        evidence={"decision": "approved"},
    )
    assert (
        connection.execute("SELECT state FROM proposals WHERE id = ?", (proposal_id,)).fetchone()[0]
        == "approved"
    )
    with pytest.raises(PermissionError, match="separate explicit"):
        transition_proposal(
            connection,
            proposal_id,
            ProposalState.APPLYING,
            actor="executor",
            evidence={},
        )
    transition_proposal(
        connection,
        proposal_id,
        ProposalState.APPLYING,
        actor="user",
        evidence={"request": "apply"},
        explicit_application_request=True,
    )
    with pytest.raises(ValueError, match="validation evidence"):
        transition_proposal(
            connection,
            proposal_id,
            ProposalState.APPLIED,
            actor="executor",
            evidence={},
        )
    transition_proposal(
        connection,
        proposal_id,
        ProposalState.APPLIED,
        actor="executor",
        evidence={"validation": ["quality command passed"]},
    )
    assert connection.execute(
        "SELECT state, entity_version FROM proposals WHERE id = ?", (proposal_id,)
    ).fetchone() == ("applied", 4)


def test_established_tool_audit_must_preserve_canonical_order_and_reasons() -> None:
    value = proposal_input()
    with pytest.raises(ValueError, match="canonical tier order"):
        replace(value, established_tool_audit=list(reversed(value.established_tool_audit)))

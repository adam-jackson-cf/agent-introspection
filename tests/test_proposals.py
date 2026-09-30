import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agent_introspection.interventions import InterventionType
from agent_introspection.proposals import (
    ProposalInput,
    ProposalState,
    TransitionProposalRequest,
    create_proposal,
    transition_proposal,
)
from agent_introspection.workflow import connect_workflow

PASSED_VALIDATION = {"validation": {"status": "passed", "checks": ["quality command passed"]}}


def proposal_database(
    state: str = "actionable", path: Path | str = ":memory:"
) -> sqlite3.Connection:
    connection = connect_workflow(path)
    connection.execute(
        """
        INSERT INTO findings (
            id, fingerprint, category, trend_state, detector_id, detector_version,
            first_seen_ns, last_seen_ns, occurrence_count, canonical_task_count,
            local_day_count, entity_version, updated_at
        ) VALUES ('finding-1', ?, 'tool_failure', ?, 'tool_failure', 1, 1, 2, 3, 2, 2, 1, 'now')
        """,
        ("f" * 64, state),
    )
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
        TransitionProposalRequest(
            proposal_id=proposal_id,
            target_state=ProposalState.APPROVED,
            actor="user",
            evidence={"decision": "approved"},
        ),
    )
    assert (
        connection.execute("SELECT state FROM proposals WHERE id = ?", (proposal_id,)).fetchone()[0]
        == "approved"
    )
    with pytest.raises(PermissionError, match="separate explicit"):
        transition_proposal(
            connection,
            TransitionProposalRequest(
                proposal_id=proposal_id,
                target_state=ProposalState.APPLYING,
                actor="executor",
                evidence={},
            ),
        )
    transition_proposal(
        connection,
        TransitionProposalRequest(
            proposal_id=proposal_id,
            target_state=ProposalState.APPLYING,
            actor="user",
            evidence={"request": "apply"},
            explicit_application_request=True,
        ),
    )
    for evidence in (
        {},
        {"validation": "failed"},
        {"validation": ["quality command failed"]},
        {"validation": {"status": "failed", "checks": ["quality command failed"]}},
        {"validation": {"status": "passed", "checks": []}},
        {"validation": {"status": "passed", "checks": [""]}},
    ):
        with pytest.raises(ValueError, match="validation evidence"):
            transition_proposal(
                connection,
                TransitionProposalRequest(
                    proposal_id=proposal_id,
                    target_state=ProposalState.APPLIED,
                    actor="executor",
                    evidence=evidence,
                ),
            )
        assert connection.execute(
            "SELECT state, entity_version FROM proposals WHERE id = ?", (proposal_id,)
        ).fetchone() == ("applying", 3)
    transition_proposal(
        connection,
        TransitionProposalRequest(
            proposal_id=proposal_id,
            target_state=ProposalState.APPLIED,
            actor="executor",
            evidence=PASSED_VALIDATION,
        ),
    )
    assert connection.execute(
        "SELECT state, entity_version FROM proposals WHERE id = ?", (proposal_id,)
    ).fetchone() == ("applied", 4)


def test_established_tool_audit_must_preserve_canonical_order_and_reasons() -> None:
    value = proposal_input()
    with pytest.raises(ValueError, match="canonical tier order"):
        replace(value, established_tool_audit=list(reversed(value.established_tool_audit)))


def audit_with(enforcing_tier: int | None) -> list[dict[str, str | bool | None]]:
    labels = (
        "Established tools of a project first.",
        "New tools second.",
        "Bespoke scripts third.",
    )
    return [
        {
            "tier": label,
            "can_enforce": index == enforcing_tier,
            "reason_unavailable": None if index == enforcing_tier else "cannot enforce",
        }
        for index, label in enumerate(labels)
    ]


@pytest.mark.parametrize(
    ("enforcing_tier", "accepted", "rejected"),
    [
        (0, InterventionType.ESTABLISHED_TOOL, InterventionType.NEW_TOOL),
        (1, InterventionType.NEW_TOOL, InterventionType.ESTABLISHED_TOOL),
        (2, InterventionType.BESPOKE_SCRIPT, InterventionType.AGENTS_GUIDANCE),
        (None, InterventionType.AGENTS_GUIDANCE, InterventionType.ESTABLISHED_TOOL),
        (None, InterventionType.IMPROVE_SKILL, "not_an_intervention"),
    ],
)
def test_intervention_type_must_follow_the_available_enforcement_tier(
    enforcing_tier: int | None, accepted: str, rejected: str
) -> None:
    value = proposal_input()
    audit = audit_with(enforcing_tier)
    replace(value, established_tool_audit=audit, intervention_type=accepted)
    with pytest.raises(ValueError, match="intervention_type"):
        replace(value, established_tool_audit=audit, intervention_type=rejected)


def test_concurrent_decisions_cannot_overwrite_each_other(tmp_path: Path) -> None:
    path = tmp_path / "workflow.sqlite3"
    first = proposal_database(path=path)
    proposal_id = create_proposal(first, proposal_input())
    second = connect_workflow(path, busy_timeout_ms=0)
    competing: list[BaseException | None] = []

    def decide_elsewhere(statement: str) -> None:
        # Run a competing decision on a separate connection at the moment the first
        # decision has read the proposal state and has not yet written.
        if competing or "FROM proposal_events" not in statement:
            return
        try:
            transition_proposal(
                second,
                TransitionProposalRequest(
                    proposal_id=proposal_id,
                    target_state=ProposalState.APPROVED,
                    actor="other-user",
                    evidence={"decision": "approve"},
                ),
            )
        except sqlite3.OperationalError as exc:
            competing.append(exc)
        else:
            competing.append(None)

    first.set_trace_callback(decide_elsewhere)
    transition_proposal(
        first,
        TransitionProposalRequest(
            proposal_id=proposal_id,
            target_state=ProposalState.REJECTED,
            actor="user",
            evidence={"decision": "reject"},
        ),
    )
    first.set_trace_callback(None)

    assert len(competing) == 1
    assert isinstance(competing[0], sqlite3.OperationalError)
    events: list[Any] = first.execute(
        "SELECT event_type FROM proposal_events WHERE proposal_id = ? ORDER BY sequence",
        (proposal_id,),
    ).fetchall()
    assert events == [("created",), ("rejected",)]
    assert first.execute(
        "SELECT state, entity_version FROM proposals WHERE id = ?", (proposal_id,)
    ).fetchone() == ("rejected", 2)

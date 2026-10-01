import json
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agent_introspection.interventions import InterventionType
from agent_introspection.proposals import (
    ProposalInput,
    ProposalState,
    SuccessMetric,
    TransitionProposalRequest,
    create_proposal,
    transition_proposal,
)
from agent_introspection.workflow import connect_workflow

SUCCESS_METRIC: dict[str, Any] = {
    "metric": "cluster_task_rate",
    "harnesses": ["codex_exec"],
    "baseline_days": 14,
    "evaluation_days": 14,
    "max_ratio": 0.5,
}

SKILL_HANDOFF: dict[str, Any] = {
    "skill_name": "release-checks",
    "workflow_owner": None,
    "ordered_steps": ["run the quality command"],
}

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
        predicted_success_metric=dict(SUCCESS_METRIC),
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
    skill = accepted in (InterventionType.IMPROVE_SKILL, InterventionType.CREATE_SKILL)
    audit = audit_with(enforcing_tier)
    value = replace(
        proposal_input(),
        established_tool_audit=audit,
        intervention_type=accepted,
        create_skill_handoff=SKILL_HANDOFF if skill else None,
    )
    with pytest.raises(ValueError, match="intervention_type"):
        replace(value, established_tool_audit=audit, intervention_type=rejected)


@pytest.mark.parametrize("kind", [InterventionType.CREATE_SKILL, InterventionType.IMPROVE_SKILL])
@pytest.mark.parametrize(
    "handoff",
    [None, {}, {"skill_name": "", "workflow_owner": None, "ordered_steps": ["a"]}],
)
def test_skill_interventions_require_a_well_formed_handoff(
    kind: InterventionType, handoff: dict[str, Any] | None
) -> None:
    with pytest.raises(ValueError, match="create_skill_handoff"):
        replace(
            proposal_input(),
            established_tool_audit=audit_with(None),
            intervention_type=kind,
            create_skill_handoff=handoff,
        )


def test_non_skill_interventions_reject_a_handoff() -> None:
    with pytest.raises(ValueError, match="create_skill_handoff"):
        replace(proposal_input(), create_skill_handoff=SKILL_HANDOFF)


@pytest.mark.parametrize(
    ("field", "mismatched"),
    [
        ("occurrence_count", lambda: replace(proposal_input(), occurrence_count=99)),
        ("task_count", lambda: replace(proposal_input(), task_count=99)),
        ("day_count", lambda: replace(proposal_input(), day_count=99)),
    ],
)
def test_proposal_counts_must_match_the_finding(
    field: str, mismatched: Callable[[], ProposalInput]
) -> None:
    connection = proposal_database()
    with pytest.raises(ValueError, match=field):
        create_proposal(connection, mismatched())
    assert connection.execute("SELECT COUNT(*) FROM proposals").fetchone() == (0,)
    assert connection.execute("SELECT COUNT(*) FROM proposal_events").fetchone() == (0,)


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


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"metric": "fewer failures"}, "metric must be one of"),
        ({"harnesses": []}, "harnesses"),
        ({"harnesses": ["codex_exec", "codex_exec"]}, "harnesses"),
        ({"baseline_days": 6}, "baseline_days"),
        ({"evaluation_days": 7.5}, "evaluation_days"),
        ({"evaluation_days": True}, "evaluation_days"),
        ({"max_ratio": 0}, "max_ratio"),
        ({"max_ratio": 1.2}, "max_ratio"),
        ({"extra": 1}, "exactly"),
    ],
)
def test_success_metric_must_be_a_structured_task_rate(
    change: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(proposal_input(), predicted_success_metric=SUCCESS_METRIC | change)


def test_free_text_success_metric_is_rejected_but_readable_as_legacy() -> None:
    not_an_object: Any = "zero bypasses in seven days"
    with pytest.raises(ValueError, match="must be an object"):
        replace(proposal_input(), predicted_success_metric=not_an_object)
    assert SuccessMetric.from_payload({"predicted_success_metric": "zero bypasses"}) is None
    metric = SuccessMetric.from_payload({
        "predicted_success_metric": SUCCESS_METRIC | {"max_ratio": 1}
    })
    assert metric is not None
    assert metric.max_ratio == pytest.approx(1.0)
    assert metric.harnesses == ("codex_exec",)


def test_success_metric_must_match_the_finding_kind_and_harnesses() -> None:
    connection = proposal_database()
    connection.execute(
        "UPDATE findings SET subject = ?",
        (json.dumps({"project": "example", "correction_kind": "ignored", "harnesses": ["omp"]}),),
    )
    connection.commit()
    with pytest.raises(ValueError, match="must be correction_task_rate"):
        create_proposal(connection, proposal_input())
    correction = SUCCESS_METRIC | {"metric": "correction_task_rate"}
    with pytest.raises(ValueError, match="not in the finding"):
        create_proposal(connection, replace(proposal_input(), predicted_success_metric=correction))
    create_proposal(
        connection,
        replace(proposal_input(), predicted_success_metric=correction | {"harnesses": ["omp"]}),
    )

"""Approval-gated proposal state transitions."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from agent_introspection.interventions import CANONICAL_TIER_LABELS, InterventionType
from agent_introspection.json_types import JsonMapping, JsonObject


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

# Deterministic interventions in canonical tier order; the remaining intervention
# types apply only when no tier can enforce the behavior.
DETERMINISTIC_INTERVENTIONS = (
    InterventionType.ESTABLISHED_TOOL,
    InterventionType.NEW_TOOL,
    InterventionType.BESPOKE_SCRIPT,
)
NON_DETERMINISTIC_INTERVENTIONS = frozenset(InterventionType) - frozenset(
    DETERMINISTIC_INTERVENTIONS
)
SKILL_INTERVENTIONS = frozenset({InterventionType.CREATE_SKILL, InterventionType.IMPROVE_SKILL})


class ProposalConflictError(RuntimeError):
    """A proposal changed between reading its state and writing a transition."""


@contextmanager
def immediate_transaction(connection: sqlite3.Connection) -> Iterator[None]:
    """Hold the write lock from the first read to commit, rolling back on failure."""
    if connection.in_transaction:
        raise RuntimeError("an immediate transaction cannot start inside an open transaction")
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        connection.rollback()
        raise
    connection.commit()


def _transaction(
    connection: sqlite3.Connection, *, outer_transaction: bool
) -> AbstractContextManager[None]:
    if not outer_transaction:
        return immediate_transaction(connection)
    if not connection.in_transaction:
        raise RuntimeError("outer_transaction requires an open transaction on the connection")
    return nullcontext()


def require_successful_validation(evidence: JsonMapping) -> None:
    """Require evidence of the form {"validation": {"status": "passed", "checks": [...]}}."""
    validation = evidence.get("validation")
    if not isinstance(validation, dict) or validation.get("status") != "passed":
        raise ValueError(
            "validation evidence is required before marking applied: "
            '{"validation": {"status": "passed", "checks": [...]}}'
        )
    checks = validation.get("checks")
    if (
        not isinstance(checks, list)
        or not checks
        or not all(isinstance(check, str) and check.strip() for check in checks)
    ):
        raise ValueError("validation evidence requires a non-empty list of passed checks")


CLUSTER_TASK_RATE = "cluster_task_rate"
CORRECTION_TASK_RATE = "correction_task_rate"
SUCCESS_METRICS = (CLUSTER_TASK_RATE, CORRECTION_TASK_RATE)
MIN_METRIC_WINDOW_DAYS = 7
_SUCCESS_METRIC_KEYS = frozenset({
    "metric",
    "harnesses",
    "baseline_days",
    "evaluation_days",
    "max_ratio",
})


@dataclass(frozen=True)
class SuccessMetric:
    """The finding's task rate after application against the rate before it.

    Success means (matching tasks per task over ``evaluation_days`` after the proposal
    was applied) <= ``max_ratio`` x (the same over ``baseline_days`` before), counted
    only for ``harnesses``. ``cluster_task_rate`` matches tasks that hit a failure
    cluster; ``correction_task_rate`` matches labelled tasks in the finding's project
    whose next prompt was a correction of the finding's kind.
    """

    metric: str
    harnesses: tuple[str, ...]
    baseline_days: int
    evaluation_days: int
    max_ratio: float

    @classmethod
    def parse(cls, value: object) -> SuccessMetric:
        """Validate a structured success metric; free-text metrics are rejected."""
        if not isinstance(value, dict) or set(value) != _SUCCESS_METRIC_KEYS:
            raise ValueError(
                "predicted_success_metric must be an object with exactly "
                f"{sorted(_SUCCESS_METRIC_KEYS)}"
            )
        if value["metric"] not in SUCCESS_METRICS:
            raise ValueError(f"predicted_success_metric.metric must be one of {SUCCESS_METRICS}")
        harnesses = value["harnesses"]
        if (
            not isinstance(harnesses, list)
            or not harnesses
            or not all(isinstance(item, str) and item.strip() for item in harnesses)
            or len(set(harnesses)) != len(harnesses)
        ):
            raise ValueError("predicted_success_metric.harnesses must be unique harness names")
        for key in ("baseline_days", "evaluation_days"):
            days = value[key]
            if isinstance(days, bool) or not isinstance(days, int) or days < MIN_METRIC_WINDOW_DAYS:
                raise ValueError(
                    f"predicted_success_metric.{key} must be an integer >= {MIN_METRIC_WINDOW_DAYS}"
                )
        ratio = value["max_ratio"]
        if isinstance(ratio, bool) or not isinstance(ratio, int | float) or not 0 < ratio <= 1:
            raise ValueError("predicted_success_metric.max_ratio must be in (0, 1]")
        return cls(
            value["metric"],
            tuple(harnesses),
            value["baseline_days"],
            value["evaluation_days"],
            float(ratio),
        )

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> SuccessMetric | None:
        """Read a stored proposal's metric; ``None`` for a legacy free-text metric."""
        try:
            return cls.parse(payload.get("predicted_success_metric"))
        except ValueError:
            return None


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
    established_tool_audit: list[dict[str, str | bool | None]]
    rejected_alternatives: list[str]
    validation_criteria: list[str]
    rollback_criteria: list[str]
    predicted_success_metric: JsonObject
    create_skill_handoff: JsonObject | None = None

    def __post_init__(self) -> None:
        SuccessMetric.parse(self.predicted_success_metric)
        tiers = tuple(entry.get("tier") for entry in self.established_tool_audit)
        if tiers != CANONICAL_TIER_LABELS:
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
        self._require_consistent_intervention_type()
        self._require_consistent_skill_handoff()

    def _require_consistent_intervention_type(self) -> None:
        enforcing = [
            intervention
            for entry, intervention in zip(
                self.established_tool_audit, DETERMINISTIC_INTERVENTIONS, strict=True
            )
            if entry.get("can_enforce")
        ]
        if enforcing:
            if self.intervention_type != enforcing[0]:
                raise ValueError(
                    f"intervention_type must be {enforcing[0]} when that tier can enforce"
                )
        elif self.intervention_type not in NON_DETERMINISTIC_INTERVENTIONS:
            allowed = ", ".join(sorted(NON_DETERMINISTIC_INTERVENTIONS))
            raise ValueError(f"intervention_type must be one of {allowed} when no tier can enforce")

    def _require_consistent_skill_handoff(self) -> None:
        is_skill = self.intervention_type in SKILL_INTERVENTIONS
        handoff = self.create_skill_handoff
        if not is_skill:
            if handoff is not None:
                raise ValueError("create_skill_handoff must be null for non-skill interventions")
            return
        if not isinstance(handoff, dict):
            raise ValueError(f"create_skill_handoff is required for {self.intervention_type}")
        if set(handoff) != {"skill_name", "workflow_owner", "ordered_steps"}:
            raise ValueError(
                "create_skill_handoff requires skill_name, workflow_owner, ordered_steps"
            )
        if not isinstance(handoff["skill_name"], str) or not handoff["skill_name"].strip():
            raise ValueError("create_skill_handoff.skill_name must be a non-empty string")
        owner = handoff["workflow_owner"]
        if owner is not None and not isinstance(owner, str):
            raise ValueError("create_skill_handoff.workflow_owner must be a string or null")
        steps = handoff["ordered_steps"]
        if not isinstance(steps, list) or not all(isinstance(s, str) and s for s in steps):
            raise ValueError("create_skill_handoff.ordered_steps must be a list of strings")


@dataclass(frozen=True)
class TransitionProposalRequest:
    proposal_id: str
    target_state: ProposalState
    actor: str
    evidence: JsonMapping
    explicit_application_request: bool = False


def _now() -> str:
    return datetime.now(UTC).isoformat()


def metric_for_subject(subject: dict[str, Any]) -> str | None:
    """Return the success metric a finding subject is measured by, if it has one."""
    if "tool_family" in subject and "failure_class" in subject:
        return CLUSTER_TASK_RATE
    if "project" in subject and "correction_kind" in subject:
        return CORRECTION_TASK_RATE
    return None


def _require_counts_match_finding(
    proposal: ProposalInput, row: sqlite3.Row | tuple[Any, ...]
) -> None:
    """Reject occurrence, task and day counts that disagree with the stored finding."""
    stored = {"occurrence_count": row[2], "task_count": row[3], "day_count": row[4]}
    for name, expected in stored.items():
        if getattr(proposal, name) != expected:
            raise ValueError(f"{name} must equal the finding's {expected}")


def _require_metric_matches_finding(proposal: ProposalInput, subject_json: str) -> None:
    """Require the metric to measure the finding's kind over harnesses it was seen in."""
    subject = json.loads(subject_json) if subject_json else {}
    metric = SuccessMetric.parse(proposal.predicted_success_metric)
    expected = metric_for_subject(subject)
    if expected is not None and metric.metric != expected:
        raise ValueError(f"success metric for this finding must be {expected}")
    observed = subject.get("harnesses")
    if not isinstance(observed, list):
        return
    unknown = sorted(set(metric.harnesses) - {str(item) for item in observed})
    if unknown:
        raise ValueError(f"success metric harnesses are not in the finding: {unknown}")


def append_proposal_event(
    connection: sqlite3.Connection,
    proposal_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Append one immutable event after the proposal's latest, inside the caller's transaction."""
    if not connection.in_transaction:
        raise RuntimeError("appending a proposal event requires an open transaction")
    sequence = connection.execute(
        "SELECT COALESCE(MAX(sequence), 0) + 1 FROM proposal_events WHERE proposal_id = ?",
        (proposal_id,),
    ).fetchone()[0]
    connection.execute(
        """
        INSERT INTO proposal_events (
            id, proposal_id, sequence, event_type, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            proposal_id,
            sequence,
            event_type,
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            _now(),
        ),
    )


def create_proposal(
    connection: sqlite3.Connection, proposal: ProposalInput, *, outer_transaction: bool = False
) -> str:
    """Persist a pending proposal and its immutable creation event.

    With ``outer_transaction`` the caller's open transaction owns commit and rollback.
    """
    proposal_id = str(uuid.uuid4())
    now = _now()
    payload = json.dumps(proposal.__dict__, sort_keys=True, separators=(",", ":"))
    with _transaction(connection, outer_transaction=outer_transaction):
        finding = connection.execute(
            "SELECT trend_state, subject, occurrence_count, canonical_task_count, "
            "local_day_count FROM findings WHERE id = ?",
            (proposal.finding_id,),
        ).fetchone()
        if finding is None:
            raise KeyError(proposal.finding_id)
        if finding[0] != "actionable":
            raise ValueError("only actionable findings can produce proposals")
        _require_metric_matches_finding(proposal, str(finding[1] or ""))
        _require_counts_match_finding(proposal, finding)
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
    return proposal_id


def transition_proposal(
    connection: sqlite3.Connection,
    request: TransitionProposalRequest,
    *,
    outer_transaction: bool = False,
) -> None:
    """Apply a valid transition while retaining an immutable event history.

    The state read and the write share one immediate transaction, and the write is
    conditioned on the state and version that were read, so concurrent decisions
    cannot overwrite each other. With ``outer_transaction`` the caller's open
    transaction owns commit and rollback, so several transitions can apply atomically.
    """
    if request.target_state is ProposalState.APPLIED:
        require_successful_validation(request.evidence)
    with _transaction(connection, outer_transaction=outer_transaction):
        row = connection.execute(
            """
            SELECT state, entity_version FROM proposals WHERE id = ?
            """,
            (request.proposal_id,),
        ).fetchone()
        if row is None:
            raise KeyError(request.proposal_id)
        source_state = ProposalState(row[0])
        if request.target_state not in ALLOWED_TRANSITIONS[source_state]:
            raise ValueError(
                f"invalid proposal transition: {source_state} -> {request.target_state}"
            )
        if (
            request.target_state is ProposalState.APPLYING
            and not request.explicit_application_request
        ):
            raise PermissionError("entering applying requires a separate explicit user request")
        expected_version = int(row[1])
        event_payload = json.dumps(
            {
                "actor": request.actor,
                "evidence": request.evidence,
                "from": source_state,
                "to": request.target_state,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        now = _now()
        sequence = connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM proposal_events WHERE proposal_id = ?",
            (request.proposal_id,),
        ).fetchone()[0]
        updated = connection.execute(
            """
            UPDATE proposals SET state = ?, updated_at = ?, entity_version = ?
            WHERE id = ? AND state = ? AND entity_version = ?
            """,
            (
                request.target_state,
                now,
                expected_version + 1,
                request.proposal_id,
                source_state,
                expected_version,
            ),
        )
        if updated.rowcount != 1:
            raise ProposalConflictError(
                f"proposal {request.proposal_id} changed during the {request.target_state} "
                "transition"
            )
        connection.execute(
            """
            INSERT INTO proposal_events (
                id, proposal_id, sequence, event_type, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                request.proposal_id,
                sequence,
                request.target_state,
                event_payload,
                now,
            ),
        )

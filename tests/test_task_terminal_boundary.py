import pytest

from experiments.dashboard_prototype.contracts import ExperimentResult
from experiments.dashboard_prototype.task_common import SUPPORTED_SURFACES, TaskExperimentId
from experiments.dashboard_prototype.task_terminal_boundary import (
    TerminalAuthorityState,
    TerminalBoundaryAudit,
    build_proof,
    current_audits,
    validate_audits,
)


def test_current_audits_are_three_producer_typed_blocked_proof() -> None:
    audits = current_audits()
    proof = build_proof("task-terminal-audit", audits)

    assert {(row.producer, row.surface) for row in audits} == set(SUPPORTED_SURFACES)
    assert {row.state for row in audits} == {TerminalAuthorityState.BLOCKED}
    assert proof.experiment_id is TaskExperimentId.TERMINAL_BOUNDARY
    assert proof.result is ExperimentResult.BLOCKED
    assert proof.metrics["terminal_candidate_population"] == 0
    assert len(proof.blocked_boundaries) == 3
    for producer in ("omp", "codex-cli", "codex-app-server"):
        assert any(
            boundary.startswith(f"{producer} lacks a separate task identity")
            and "terminal task outcome hook or telemetry boundary" in boundary
            for boundary in proof.blocked_boundaries
        )


@pytest.mark.parametrize(
    "inferred_outcome",
    [
        "success",
        "failure",
        "timeout",
        "cancellation",
        "lifecycle-session-end",
        "final-tool-success",
        "absence-of-errors",
        "response-text",
    ],
)
def test_outcome_authority_cannot_be_inferred(inferred_outcome: str) -> None:
    with pytest.raises(ValueError, match="fail closed"):
        TerminalBoundaryAudit(
            "omp",
            "omp",
            inferred_outcome,  # type: ignore[arg-type]
            "lifecycle ended without task authority",
        )


def test_validation_rejects_missing_and_duplicate_producer_audits() -> None:
    audits = current_audits()
    with pytest.raises(ValueError, match="omits or adds"):
        validate_audits(audits[:-1])
    with pytest.raises(ValueError, match="duplicated"):
        validate_audits((*audits, audits[0]))

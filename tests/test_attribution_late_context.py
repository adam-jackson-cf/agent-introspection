from datetime import UTC, datetime, timedelta

import pytest

from experiments.dashboard_prototype.attribution_late_context import (
    ActivityVersion,
    build_late_context_proof,
    reduce_late_context,
)
from experiments.dashboard_prototype.contracts import EvidenceProvenance, ExperimentResult

START = datetime(2026, 9, 1, tzinfo=UTC)
END = START + timedelta(hours=1)
SOURCE = START + timedelta(minutes=30)


def _version(
    version: int, state: str, event: str, *, activity: str = "activity"
) -> ActivityVersion:
    return ActivityVersion(
        activity_id=activity,
        version=version,
        event_id=event,
        producer="codex-app-server",
        surface="codex-app-server",
        source_time_ns=int(SOURCE.timestamp()) * 1_000_000_000,
        attribution_state=state,
        attribution_method="lifecycle",
        reason_code="missing_context" if state == "unresolved" else None,
        project_id="a" * 64 if state == "resolved" else None,
    )


def test_blocks_rate_without_remote_denominator_authority() -> None:
    reduction = reduce_late_context(
        (_version(1, "unresolved", "event-1"), _version(2, "resolved", "event-2")),
        start=START,
        end=END,
    )

    assert reduction.transition_count == reduction.ever_unresolved_count == 1
    assert reduction.transitions[0].resolved_event_id != "event-2"
    proof = build_late_context_proof(
        "run-1", EvidenceProvenance.FRESH_REAL, "boundary", reduction, remote_denominator=None
    )
    assert proof.result is ExperimentResult.BLOCKED
    assert proof.metrics["transition_count"] == 1
    assert proof.metrics["late_context_rate"] == 0.0


def test_rejects_gaps_duplicate_events_and_superseded_transition() -> None:
    reduction = reduce_late_context(
        (
            _version(1, "unresolved", "gap-1", activity="gap"),
            _version(3, "resolved", "gap-3", activity="gap"),
            _version(1, "unresolved", "duplicate", activity="one"),
            _version(2, "resolved", "duplicate", activity="two"),
            _version(1, "unresolved", "superseded-1", activity="superseded"),
            _version(2, "resolved", "superseded-2", activity="superseded"),
            _version(3, "resolved", "superseded-3", activity="superseded"),
        ),
        start=START,
        end=END,
    )

    assert reduction.invalid_activity_count == 2
    assert reduction.transition_count == 0
    proof = build_late_context_proof(
        "run-1", EvidenceProvenance.FRESH_REAL, "boundary", reduction, remote_denominator=0
    )
    assert proof.result is ExperimentResult.FAILED


def test_rejects_unresolved_version_without_canonical_reason() -> None:
    with pytest.raises(ValueError, match="rejection reason"):
        ActivityVersion(
            activity_id="activity",
            version=1,
            event_id="event",
            producer="omp",
            surface="omp",
            source_time_ns=int(SOURCE.timestamp()) * 1_000_000_000,
            attribution_state="unresolved",
            attribution_method="lifecycle",
            reason_code=None,
            project_id=None,
        )

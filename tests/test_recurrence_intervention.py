from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from experiments.dashboard_prototype.recurrence_intervention import (
    ORDERED_TIERS,
    ApplicationState,
    ApprovalState,
    InterventionSelection,
    ObservationWindow,
    VersionedIdentity,
    reduce_m15,
    transition_selection,
)

START = datetime(2026, 9, 2, 12, tzinfo=UTC)


def _selection(index: int = 0, *, applied: bool = True) -> InterventionSelection:
    tier = ORDERED_TIERS[index]
    return InterventionSelection(
        VersionedIdentity("finding-1", 2),
        VersionedIdentity("practice-1", 3),
        tier,
        dict.fromkeys(ORDERED_TIERS[:index], "not_available"),
        ApprovalState.APPROVED if applied else ApprovalState.PENDING,
        ApplicationState.APPLIED if applied else ApplicationState.NOT_APPLIED,
        START if applied else None,
    )


def _window(start: datetime, occurrences: int, tasks: int, **exposure: str) -> ObservationWindow:
    values = {
        "evidence": "finding-1",
        "project": "project-1",
        "producer": "omp",
        "model": "model-1",
        "task_class": "task-class-1",
        **exposure,
    }
    return ObservationWindow(
        start,
        start + timedelta(days=7),
        occurrences,
        tasks,
        values["evidence"],
        values["project"],
        values["producer"],
        values["model"],
        values["task_class"],
    )


def _comparable_windows() -> tuple[ObservationWindow, ObservationWindow]:
    return _window(START - timedelta(days=7), 2, 10), _window(START, 1, 10)


@pytest.mark.parametrize("index", range(len(ORDERED_TIERS)))
def test_every_ordered_tier_has_exact_earlier_rejections(index: int) -> None:
    selection = _selection(index)

    assert selection.selected_tier is ORDERED_TIERS[index]
    assert tuple(selection.rejection_reasons) == ORDERED_TIERS[:index]


def test_missing_earlier_rejection_is_rejected() -> None:
    with pytest.raises(ValueError, match="earlier enforcement tier"):
        InterventionSelection(
            VersionedIdentity("finding-1", 1),
            VersionedIdentity("practice-1", 1),
            ORDERED_TIERS[2],
            {ORDERED_TIERS[0]: "not_available"},
            ApprovalState.APPROVED,
            ApplicationState.APPLIED,
            START,
        )


def test_m15_calculates_and_retains_lineage_and_exact_boundaries() -> None:
    pre, post = _comparable_windows()
    result = reduce_m15(_selection(), pre, post)

    assert result.state == "calculated"
    assert result.comparable
    assert result.pre_rate == 0.2
    assert result.post_rate == 0.1
    assert result.relative_change == -0.5
    assert result.evidence_fingerprint == "finding-1"
    assert result.practice == VersionedIdentity("practice-1", 3)
    assert result.project_fingerprint == "project-1"
    assert result.applied_at == result.pre_end == result.post_start
    assert result.pre_occurrences == 2
    assert result.post_tasks == 10


def test_m15_suppresses_unequal_windows_and_exposure_mismatch() -> None:
    pre, post = _comparable_windows()
    unequal = replace(pre, start=pre.start + timedelta(days=1))
    mismatch = replace(post, model="model-2")

    unequal_result = reduce_m15(_selection(), unequal, post)
    mismatch_result = reduce_m15(_selection(), pre, mismatch)

    assert unequal_result.suppression_reason == "window_duration_mismatch"
    assert mismatch_result.suppression_reason == "exposure_identity_mismatch"
    assert not unequal_result.comparable
    assert not mismatch_result.comparable


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("project_fingerprint", "project-2"),
        ("producer", "codex-cli"),
        ("model", "model-2"),
        ("task_class", "task-class-2"),
    ],
)
def test_m15_suppresses_every_exposure_identity_mismatch(field: str, value: str) -> None:
    pre, post = _comparable_windows()
    if field == "evidence_fingerprint":
        changed = replace(post, evidence_fingerprint=value)
    elif field == "project_fingerprint":
        changed = replace(post, project_fingerprint=value)
    elif field == "producer":
        changed = replace(post, producer=value)
    elif field == "model":
        changed = replace(post, model=value)
    else:
        changed = replace(post, task_class=value)

    result = reduce_m15(_selection(), pre, changed)

    assert result.suppression_reason == "exposure_identity_mismatch"


def test_m15_suppresses_when_evidence_is_not_the_selected_finding() -> None:
    pre, post = _comparable_windows()

    result = reduce_m15(_selection(), replace(pre, evidence_fingerprint="finding-2"), post)

    assert result.suppression_reason == "finding_evidence_mismatch"
    assert result.evidence_fingerprint == "finding-2"


def test_m15_requires_application_as_exact_window_boundary() -> None:
    pre, post = _comparable_windows()

    pre_result = reduce_m15(
        _selection(),
        replace(pre, end=pre.end - timedelta(seconds=1)),
        post,
    )
    post_result = reduce_m15(
        _selection(),
        pre,
        replace(post, start=post.start + timedelta(seconds=1)),
    )

    assert pre_result.suppression_reason == "pre_application_boundary_mismatch"
    assert post_result.suppression_reason == "post_application_boundary_mismatch"


def test_m15_marks_pre_zero_noncalculable() -> None:
    pre, post = _comparable_windows()
    result = reduce_m15(_selection(), replace(pre, occurrences=0), post)

    assert result.state == "pre_zero"
    assert result.relative_change is None
    assert result.suppression_reason == "pre_zero_noncalculable"


def test_approval_and_application_transitions_are_explicit() -> None:
    pending = _selection(applied=False)
    applied = transition_selection(pending, ApprovalState.APPROVED, ApplicationState.APPLIED, START)

    assert applied.application_state is ApplicationState.APPLIED
    with pytest.raises(ValueError, match="terminal"):
        transition_selection(applied, ApprovalState.APPROVED, ApplicationState.NOT_APPLIED, None)

from datetime import UTC, datetime, timedelta

import pytest

from agent_introspection.trends import Occurrence, TrendState, evaluate_findings


def occurrence(finding: str, task: str | None, when: datetime, sequence: int) -> Occurrence:
    return Occurrence(
        observation_id=f"o{sequence}",
        finding_id=finding,
        occurred_at_ns=int(when.timestamp() * 1_000_000_000),
        canonical_task_id=task,
    )


def test_trend_state_machine_uses_tasks_days_and_seven_day_window() -> None:
    now = datetime(2026, 7, 10, 12, tzinfo=UTC)
    rows = [
        occurrence("actionable", "t1", now - timedelta(days=2), 1),
        occurrence("actionable", "t2", now - timedelta(days=1), 2),
        occurrence("actionable", "t2", now, 3),
        occurrence("emerging", "t1", now - timedelta(hours=2), 4),
        occurrence("emerging", "t1", now - timedelta(hours=1), 5),
        occurrence("isolated", None, now, 6),
        occurrence("expired", "t1", now - timedelta(days=8), 7),
    ]
    states = {row.finding_id: row for row in evaluate_findings(rows, now=now)}
    assert states["actionable"].state is TrendState.ACTIONABLE
    assert states["actionable"].canonical_task_count == 2
    assert states["actionable"].local_day_count >= 2
    assert states["emerging"].state is TrendState.EMERGING
    assert states["isolated"].state is TrendState.ISOLATED
    assert "expired" not in states


def test_previously_actionable_absence_becomes_dormant_without_deletion() -> None:
    now = datetime(2026, 7, 10, 12, tzinfo=UTC)
    result = evaluate_findings([], now=now, previously_actionable={"known"})
    assert result[0].finding_id == "known"
    assert result[0].state is TrendState.DORMANT


def test_trends_require_timezone_aware_clock() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_findings([], now=datetime(2026, 7, 10, 12))

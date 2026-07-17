"""Deterministic trend evaluation for observation findings."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")


class TrendState(StrEnum):
    ISOLATED = "isolated"
    EMERGING = "emerging"
    ACTIONABLE = "actionable"
    DORMANT = "dormant"


@dataclass(frozen=True)
class Occurrence:
    observation_id: str
    finding_id: str
    occurred_at_ns: int
    canonical_task_id: str | None

    @property
    def occurred_at(self) -> datetime:
        return datetime.fromtimestamp(self.occurred_at_ns / 1_000_000_000, tz=UTC)


@dataclass(frozen=True)
class TrendEvaluation:
    finding_id: str
    state: TrendState
    occurrence_count: int
    canonical_task_count: int
    local_day_count: int
    window_started_at_ns: int
    window_ended_at_ns: int


def evaluate_findings(
    occurrences: list[Occurrence],
    *,
    now: datetime,
    previously_actionable: set[str] | None = None,
) -> list[TrendEvaluation]:
    """Evaluate findings against the canonical seven-day thresholds."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now_utc = now.astimezone(UTC)
    window_start = now_utc - timedelta(days=7)
    grouped: dict[str, list[Occurrence]] = defaultdict(list)
    for occurrence in occurrences:
        if window_start <= occurrence.occurred_at <= now_utc:
            grouped[occurrence.finding_id].append(occurrence)

    prior = previously_actionable or set()
    finding_ids = set(grouped) | prior
    evaluations: list[TrendEvaluation] = []
    for finding_id in sorted(finding_ids):
        rows = grouped.get(finding_id, [])
        tasks = {row.canonical_task_id for row in rows if row.canonical_task_id is not None}
        days = {row.occurred_at.astimezone(LONDON).date() for row in rows}
        count = len(rows)
        actionable = (count >= 3 and len(tasks) >= 2 and len(days) >= 2) or (
            count >= 5 and len(tasks) >= 3
        )
        if actionable:
            state = TrendState.ACTIONABLE
        elif not rows and finding_id in prior:
            state = TrendState.DORMANT
        elif count <= 1:
            state = TrendState.ISOLATED
        else:
            state = TrendState.EMERGING
        evaluations.append(
            TrendEvaluation(
                finding_id=finding_id,
                state=state,
                occurrence_count=count,
                canonical_task_count=len(tasks),
                local_day_count=len(days),
                window_started_at_ns=int(window_start.timestamp() * 1_000_000_000),
                window_ended_at_ns=int(now_utc.timestamp() * 1_000_000_000),
            )
        )
    return evaluations

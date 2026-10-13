"""Refresher schedules: the minute map that keeps heavy queries from overlapping."""

from __future__ import annotations

import itertools
import re
from datetime import UTC, datetime, timedelta

import pytest

from agent_introspection import facts


def test_history_refreshers_run_one_at_a_time_off_the_shared_ten_minute_marks() -> None:
    offsets: list[int] = []
    for snapshot in facts.SNAPSHOTS:
        statements = facts.snapshot_statements(snapshot)
        history, recent = [s for s in statements if s.startswith("CREATE MATERIALIZED VIEW")]
        match = re.search(r"REFRESH EVERY 1 DAY OFFSET (\d+) MINUTE DEPENDS", history)
        assert match
        offsets.append(int(match.group(1)))
        for refresher in (history, recent):
            assert f"\nSETTINGS {facts.REFRESH_RETRY_SETTINGS}\nTO " in refresher
    # Strictly increasing in SNAPSHOTS order (so a dependency, which must come earlier
    # in that order, builds first) and never on a minute shared with other refreshers.
    assert all(a < b for a, b in itertools.pairwise(offsets))
    assert len(set(offsets)) == len(offsets)
    assert all(offset % 10 == 5 for offset in offsets)


def test_recent_refreshers_take_distinct_minutes_off_loaders_history_and_sweeps() -> None:
    offsets: list[int] = []
    for snapshot in facts.SNAPSHOTS:
        recent = facts.snapshot_statements(snapshot)[-1]
        match = re.search(r"REFRESH EVERY 10 MINUTE OFFSET (\d+) MINUTE DEPENDS", recent)
        assert match
        offsets.append(int(match.group(1)))
    taken = {0, facts.HISTORY_FIRST_OFFSET_MINUTES % 10, 9}
    assert len(set(offsets)) == len(offsets)
    assert all(a < b for a, b in itertools.pairwise(offsets))
    assert all(1 <= offset <= 9 for offset in offsets)
    assert not taken & set(offsets)
    # task_labels depends on task_outcomes, which ClickHouse waits for only if earlier.
    by_view = dict(zip((s.view for s in facts.SNAPSHOTS), offsets, strict=True))
    assert by_view["task_labels"] > by_view["task_outcomes"]


@pytest.mark.parametrize("hour", [0, 0.4, 0.6, 1.5, 1.7, 12, 23.9])
def test_history_and_recent_both_cover_the_boundary_between_rebuilds(hour: float) -> None:
    """History views rebuild daily from 00:35 to 01:35 UTC and recent every 10 minutes;
    whenever the dashboard reads, the boundary must lie inside both parts or rows go missing."""
    day = timedelta(days=1)
    now = datetime(2026, 10, 2, tzinfo=UTC) + timedelta(hours=hour)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # Before the last history build, history may be yesterday's; recent may be 10 minutes old.
    last_offset = max(
        facts.HISTORY_FIRST_OFFSET_MINUTES + facts.HISTORY_OFFSET_STEP_MINUTES * index
        for index in range(len(facts.SNAPSHOTS))
    )
    history_built = today + timedelta(minutes=last_offset)
    if history_built > now:
        history_built -= day
    recent_built = now - timedelta(minutes=10)

    def start_of(moment: datetime) -> datetime:
        return moment.replace(hour=0, minute=0, second=0, microsecond=0)

    boundary = today - facts.BOUNDARY_DAYS * day
    history_end = start_of(history_built) - facts.HISTORY_END_DAYS * day
    recent_start = start_of(recent_built) - facts.RECENT_START_DAYS * day

    assert recent_start <= boundary <= history_end

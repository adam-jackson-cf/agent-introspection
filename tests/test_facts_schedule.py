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
        assert f"\nSETTINGS {facts.HISTORY_RETRY_SETTINGS}\nTO " in history
        assert f"\nSETTINGS {facts.RECENT_RETRY_SETTINGS}\nTO " in recent
    # Strictly increasing in SNAPSHOTS order (so a dependency, which must come earlier
    # in that order, builds first) and never on a minute shared with other refreshers.
    assert all(a < b for a, b in itertools.pairwise(offsets))
    assert len(set(offsets)) == len(offsets)
    assert all(offset % 10 == 5 for offset in offsets)


def _minute_class(schedule: str) -> int:
    """Return the minute within a 10-minute period that a schedule fires on."""
    match = re.search(r"OFFSET (\d+) MINUTE", schedule)
    return int(match.group(1)) % 10 if match else 0


def test_retries_wait_one_period_so_they_stay_in_their_own_minute_class() -> None:
    period = facts.RECENT_PERIOD_MINUTES * 60_000
    for settings in (
        facts.LOADER_RETRY_SETTINGS,
        facts.RECENT_RETRY_SETTINGS,
        facts.HISTORY_RETRY_SETTINGS,
    ):
        assert f"refresh_retry_initial_backoff_ms = {period}" in settings
        assert f"refresh_retry_max_backoff_ms = {period}" in settings
    assert facts.LOADER_RETRY_SETTINGS.startswith("refresh_retries = 2,")
    assert facts.RECENT_RETRY_SETTINGS.startswith("refresh_retries = 2,")


def test_sweeps_each_own_a_distinct_hour_offset_in_the_x9_class() -> None:
    sweeps = {x.name: x.schedule for x in facts.LOADERS if x.name.startswith("sweep_")}
    assert sweeps == {
        "sweep_spans": "EVERY 1 HOUR OFFSET 9 MINUTE",
        "sweep_logs": "EVERY 1 HOUR OFFSET 19 MINUTE",
        "sweep_hook_events": "EVERY 1 HOUR OFFSET 29 MINUTE",
        "sweep_session_projects": "EVERY 1 HOUR OFFSET 39 MINUTE",
    }
    assert {_minute_class(s) for s in sweeps.values()} == {9}


def test_recent_refreshers_take_distinct_minutes_off_loaders_history_and_sweeps() -> None:
    recents = {s.view: _minute_class(facts.recent_schedule(s)) for s in facts.SNAPSHOTS}
    taken = {
        *(_minute_class(x.schedule) for x in facts.LOADERS),
        *(_minute_class(facts.history_schedule(s)) for s in facts.SNAPSHOTS),
    }
    assert taken == {0, 5, 9}
    assert len(set(recents.values())) == len(recents)
    assert not taken & set(recents.values())
    for snapshot in facts.SNAPSHOTS:
        recent = facts.snapshot_statements(snapshot)[-1]
        assert f"REFRESH {facts.recent_schedule(snapshot)} DEPENDS" in recent
    # A dependency is waited for only when its offset is earlier; labels follow outcomes.
    assert recents["task_labels"] == recents["task_outcomes"] + 1


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


def test_install_refreshes_loaders_then_histories_then_recents_one_at_a_time() -> None:
    ran: list[str] = []

    def run(sql: str) -> str:
        ran.append(sql)
        return ""

    facts.install(run)

    live = ran[ran.index("DROP DATABASE IF EXISTS introspection_verify SYNC", 1) + 1 :]
    created = max(i for i, s in enumerate(live) if s.startswith("CREATE MATERIALIZED VIEW"))
    tail = [s for s in live[created + 1 :] if s.startswith("SYSTEM")]
    names = [
        *(loader.name for loader in facts.LOADERS if loader.schedule == facts.MINUTE_SCHEDULE),
        *(facts.snapshot_refreshers(s)[0] for s in facts.SNAPSHOTS),
        *(facts.snapshot_refreshers(s)[1] for s in facts.SNAPSHOTS),
    ]
    assert tail == [
        f"SYSTEM {verb} VIEW introspection.{name}" for name in names for verb in ("REFRESH", "WAIT")
    ]
    # Sweeps are created EMPTY and left to their slot, never refreshed at install.
    assert not [s for s in live if s.startswith("SYSTEM") and "sweep_" in s]
    assert all(" EMPTY\nAS" in s for s in live if s.startswith("CREATE MATERIALIZED VIEW"))


def test_install_fails_naming_the_refresher_whose_refresh_ended_in_an_exception() -> None:
    bad = "refresh_task_outcomes_history"

    def run(sql: str) -> str:
        if "system.view_refreshes" in sql and f"view = '{bad}'" in sql:
            return '{"exception":"Code: 241. Memory limit exceeded"}\n'
        return ""

    with pytest.raises(facts.FactsError, match=rf"refresh of {bad} failed: Code: 241"):
        facts.install(run)

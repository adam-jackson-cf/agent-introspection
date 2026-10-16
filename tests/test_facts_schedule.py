"""Refresher schedules: the minute map that keeps heavy queries from overlapping."""

from __future__ import annotations

import itertools
import json
import os
import re
from collections.abc import Callable
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


def _backoff_minutes(settings: str, key: str) -> float:
    match = re.search(rf"{key} = (\d+)", settings)
    assert match
    return int(match.group(1)) / 60_000


def test_retry_backoffs_clear_the_slots_each_refresher_class_could_land_on() -> None:
    # Backoff counts from the failed attempt's end, so a history retry must clear the
    # whole block of later histories (plus a 10-minute margin for the attempt itself).
    block = (len(facts.SNAPSHOTS) - 1) * facts.HISTORY_OFFSET_STEP_MINUTES + 10
    for key in ("initial", "max"):
        history = _backoff_minutes(facts.HISTORY_RETRY_SETTINGS, f"refresh_retry_{key}_backoff_ms")
        assert history >= block
        assert history % 10 == 0
        assert _backoff_minutes(facts.SWEEP_RETRY_SETTINGS, f"refresh_retry_{key}_backoff_ms") >= 60
        assert _backoff_minutes(facts.LOADER_RETRY_SETTINGS, f"refresh_retry_{key}_backoff_ms") < 2
    assert facts.LOADER_RETRY_SETTINGS.startswith("refresh_retries = 3,")
    assert facts.RECENT_RETRY_SETTINGS.startswith("refresh_retries = 2,")


def test_each_loader_uses_the_retry_settings_of_its_class() -> None:
    for loader in facts.LOADERS:
        expected = (
            facts.LOADER_RETRY_SETTINGS
            if loader.schedule == facts.MINUTE_SCHEDULE
            else facts.SWEEP_RETRY_SETTINGS
        )
        assert f"\nSETTINGS {expected}\nAPPEND TO " in facts.loader_ddl(loader)


def test_sweeps_each_own_a_distinct_hour_offset_in_the_x9_class() -> None:
    sweeps = {x.name: x.schedule for x in facts.LOADERS if x.name.startswith("sweep_")}
    assert sweeps == {
        "sweep_spans": "EVERY 1 HOUR OFFSET 9 MINUTE",
        "sweep_logs": "EVERY 1 HOUR OFFSET 19 MINUTE",
        "sweep_hook_events": "EVERY 1 HOUR OFFSET 29 MINUTE",
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


def _no_sleep(_seconds: float) -> None:
    return None


def test_install_refreshes_loaders_then_histories_then_recents_one_at_a_time() -> None:
    ran: list[str] = []

    def run(sql: str) -> str:
        ran.append(sql)
        return ""

    facts.install(run, sleep=_no_sleep)

    created = max(i for i, s in enumerate(ran) if s.startswith("CREATE MATERIALIZED VIEW"))
    live_start = max(i for i, s in enumerate(ran) if s.startswith("DROP DATABASE IF EXISTS"))
    assert live_start < created
    tail = [s for s in ran[created + 1 :] if s.startswith("SYSTEM")]
    names = [
        *(loader.name for loader in facts.LOADERS if loader.schedule == facts.MINUTE_SCHEDULE),
        *(facts.snapshot_refreshers(s)[0] for s in facts.SNAPSHOTS),
        *(facts.snapshot_refreshers(s)[1] for s in facts.SNAPSHOTS),
    ]
    assert tail == [
        f"SYSTEM {verb} VIEW introspection.{name}" for name in names for verb in ("REFRESH", "WAIT")
    ]
    # Sweeps are created EMPTY and left to their slot, never refreshed at install.
    assert not [s for s in ran[created + 1 :] if s.startswith("SYSTEM") and "sweep_" in s]
    assert all(" EMPTY\nAS" in s for s in ran if s.startswith("CREATE MATERIALIZED VIEW"))


def _failing_waits(failing: set[str], attempts: dict[str, int]) -> facts.SqlRunner:
    def run(sql: str) -> str:
        if sql.startswith("SYSTEM WAIT VIEW introspection."):
            name = sql.rsplit(".", 1)[1]
            attempts[name] = attempts.get(name, 0) + 1
            if name in failing:
                raise facts.FactsError("REFRESH_FAILED: Code: 241. Memory limit exceeded")
        return ""

    return run


def test_install_attempts_every_refresher_and_reports_all_failures_after_one_retry() -> None:
    bad = {"refresh_tool_calls_history", "refresh_model_calls_recent", "load_spans"}
    attempts: dict[str, int] = {}
    pauses: list[float] = []

    with pytest.raises(facts.FactsError) as raised:
        facts.install(_failing_waits(bad, attempts), sleep=pauses.append)

    for name in bad:
        assert f"refresh of {name} failed: REFRESH_FAILED" in str(raised.value)
        assert attempts[name] == 2
    assert pauses == [facts.REFRESH_RETRY_PAUSE_SECONDS] * len(bad)
    # Everything else was still refreshed, once, after the failures.
    assert attempts["refresh_model_call_attempts_recent"] == 1
    assert attempts["refresh_task_labels_recent"] == 1


def test_a_refresh_that_succeeds_on_retry_is_not_reported() -> None:
    attempts: dict[str, int] = {}

    def run(sql: str) -> str:
        if sql.startswith("SYSTEM WAIT VIEW"):
            name = sql.rsplit(".", 1)[1]
            attempts[name] = attempts.get(name, 0) + 1
            if name == "load_logs" and attempts[name] == 1:
                raise facts.FactsError("memory burst")
        return ""

    facts.install(run, sleep=_no_sleep)
    assert attempts["load_logs"] == 2


def test_a_snapshot_part_is_skipped_when_the_part_it_reads_failed() -> None:
    attempts: dict[str, int] = {}
    run = _failing_waits({"refresh_task_outcomes_recent"}, attempts)

    with pytest.raises(facts.FactsError) as raised:
        facts.refresh_snapshots(run, _no_sleep)

    message = str(raised.value)
    assert "refresh of refresh_task_outcomes_recent failed" in message
    assert (
        "refresh of refresh_task_labels_recent skipped: refresh_task_outcomes_recent failed"
        in message
    )
    assert "refresh_task_labels_recent" not in attempts
    assert attempts["refresh_task_labels_history"] == 1
    assert attempts["refresh_model_call_attempts_recent"] == 1


def test_refresh_named_refreshes_one_known_view_and_rejects_others() -> None:
    ran: list[str] = []

    def run(sql: str) -> str:
        ran.append(sql)
        return ""

    assert facts.refresh_named(run, "refresh_tool_calls_recent", _no_sleep) == {
        "refreshed": "refresh_tool_calls_recent"
    }
    assert ran == [
        "SYSTEM REFRESH VIEW introspection.refresh_tool_calls_recent",
        "SYSTEM WAIT VIEW introspection.refresh_tool_calls_recent",
    ]
    with pytest.raises(ValueError, match="unknown refresher nope"):
        facts.refresh_named(run, "nope", _no_sleep)
    assert len(ran) == 2


def test_refresh_view_keeps_waiting_through_runner_timeouts_within_its_budget() -> None:
    waits = 0
    now = 0.0

    def clock() -> float:
        return now

    def run(sql: str) -> str:
        nonlocal waits, now
        if sql.startswith("SYSTEM WAIT"):
            waits += 1
            now += 600
            if waits < 3:
                raise facts.StatementTimeoutError("ClickHouse statement timed out")
        return ""

    facts.refresh_view(run, "load_spans", budget=1800, clock=clock)
    assert waits == 3


def test_refresh_view_fails_once_the_budget_is_spent_still_running() -> None:
    now = 0.0

    def clock() -> float:
        return now

    def run(sql: str) -> str:
        nonlocal now
        if sql.startswith("SYSTEM WAIT"):
            now += 600
            raise facts.StatementTimeoutError("ClickHouse statement timed out")
        return ""

    with pytest.raises(facts.FactsError, match="refresh of load_spans failed: still running"):
        facts.refresh_view(run, "load_spans", budget=1800, clock=clock)


def test_refresh_view_does_not_query_state_after_a_successful_wait() -> None:
    ran: list[str] = []

    def run(sql: str) -> str:
        ran.append(sql)
        return ""

    facts.refresh_view(run, "load_spans")
    assert not [s for s in ran if "system.view_refreshes" in s]


def test_verify_uses_a_scratch_database_per_run_and_drops_only_stale_ones() -> None:
    now = 1_000_000.0
    fresh = f"introspection_verify_{int(now) - 60}_1"
    stale = f"introspection_verify_{int(now) - 7200}_2"
    existing = ["introspection_verify", fresh, stale, "introspection"]
    ran: list[str] = []

    def run(sql: str) -> str:
        ran.append(sql)
        if "system.databases" in sql:
            return "".join(json.dumps({"name": name}) + "\n" for name in existing)
        return ""

    first = facts.verify(run, clock=lambda: now)
    assert facts.verify(run, clock=lambda: now + 1)["database"] != first["database"]

    dropped = {s.split()[4] for s in ran if s.startswith("DROP DATABASE") and "_verify" in s}
    assert {"introspection_verify", stale} <= dropped
    assert fresh not in dropped
    assert "introspection" not in {s.split()[4] for s in ran if s.startswith("DROP DATABASE")}
    # Each run builds and drops its own database, and the name embeds time and pid.
    assert first["database"] == f"introspection_verify_{int(now)}_{os.getpid()}"
    assert ran.count(f"DROP DATABASE IF EXISTS {first['database']} SYNC") == 2


def test_refresh_with_retry_does_not_requeue_a_refresh_that_is_still_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[str] = []
    slept: list[float] = []

    def refresh_view(_run: object, name: str) -> None:
        attempts.append(name)
        raise facts.RefreshStillRunningError(f"refresh of {name} failed: still running")

    monkeypatch.setattr(facts, "refresh_view", refresh_view)
    with pytest.raises(facts.RefreshStillRunningError):
        facts.refresh_with_retry(lambda _sql: "", "load_spans", slept.append)
    assert attempts == ["load_spans"]
    assert slept == []

    with pytest.raises(facts.FactsError, match="refresh of load_spans failed: still running"):
        facts.refresh_all(lambda _sql: "", [("load_spans", "")], slept.append)
    assert attempts == ["load_spans", "load_spans"]


def test_refresh_view_still_running_error_is_the_budget_exhaustion_type() -> None:
    now = 0.0

    def run(sql: str) -> str:
        nonlocal now
        if sql.startswith("SYSTEM WAIT"):
            now += 600
            raise facts.StatementTimeoutError("ClickHouse statement timed out")
        return ""

    with pytest.raises(facts.RefreshStillRunningError):
        facts.refresh_view(run, "load_spans", budget=1800, clock=lambda: now)


def test_verify_keeps_a_recently_modified_legacy_scratch_database() -> None:
    now = 1_000_000.0

    def make_run(modified: int | None, ran: list[str]) -> Callable[[str], str]:
        def run(sql: str) -> str:
            ran.append(sql)
            if "system.databases" in sql:
                return json.dumps({"name": "introspection_verify"}) + "\n"
            if "system.tables" in sql:
                return json.dumps({"modified": modified}) + "\n"
            return ""

        return run

    busy: list[str] = []
    assert facts.drop_stale_scratch_databases(make_run(int(now) - 60, busy), now) == []
    assert "DROP DATABASE IF EXISTS introspection_verify SYNC" not in busy

    for modified in (int(now) - 7200, None):
        ran: list[str] = []
        assert facts.drop_stale_scratch_databases(make_run(modified, ran), now) == [
            "introspection_verify"
        ]

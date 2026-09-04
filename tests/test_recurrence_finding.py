from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from experiments.dashboard_prototype.contracts import PrototypeContractError
from experiments.dashboard_prototype.recurrence_finding import (
    FindingCandidate,
    FindingEvent,
    FindingReductionBoundary,
    reduce_findings,
)

FINGERPRINT = "a" * 64
LONDON = ZoneInfo("Europe/London")


def event(
    event_id: str, task: str, when: datetime, version: int = 1, detector: str = "detector"
) -> FindingEvent:
    return FindingEvent("omp", detector, 1, FINGERPRINT, task, event_id, version, when)


def boundary(
    *events: FindingEvent, start: datetime = datetime(2026, 3, 25, tzinfo=LONDON)
) -> FindingReductionBoundary:
    end = datetime.combine(start.astimezone(LONDON).date() + timedelta(days=7), time.min, LONDON)
    return FindingReductionBoundary(start, end, events)


def test_m14_first_alternative_uses_distinct_london_days_and_tasks() -> None:
    rows = reduce_findings(
        boundary=boundary(
            event("event.one", "task.one", datetime(2026, 3, 28, 23, 30, tzinfo=UTC)),
            event("event.two", "task.two", datetime(2026, 3, 29, 1, 30, tzinfo=UTC)),
            event("event.three", "task.two", datetime(2026, 3, 30, 0, 30, tzinfo=UTC)),
        )
    )
    assert (rows[0].occurrence_count, rows[0].canonical_task_count, rows[0].london_day_count) == (
        3,
        2,
        3,
    )
    assert rows[0].actionable is True


def test_m14_second_alternative_does_not_require_two_london_days() -> None:
    start = datetime(2026, 10, 24, tzinfo=LONDON)
    rows = reduce_findings(
        boundary=boundary(
            *(
                event(f"event.{index}", f"task.{index % 3}", start + timedelta(hours=index))
                for index in range(1, 6)
            ),
            start=start,
        )
    )
    assert rows[0].london_day_count == 1
    assert rows[0].actionable is True


@pytest.mark.parametrize(
    ("start", "hours"),
    [(datetime(2026, 3, 25, tzinfo=LONDON), 167), (datetime(2026, 10, 24, tzinfo=LONDON), 169)],
)
def test_london_calendar_windows_cover_dst_transitions(start: datetime, hours: int) -> None:
    window = boundary(start=start)
    assert window.source_end - window.source_start == timedelta(hours=hours)


def test_local_midnight_and_start_exclusive_end_inclusive_membership() -> None:
    start = datetime(2026, 10, 24, tzinfo=LONDON)
    window = boundary(start=start)
    reduce_findings(
        boundary=boundary(event("event.end", "task.one", window.source_end), start=start)
    )
    with pytest.raises(PrototypeContractError, match="midnights"):
        FindingReductionBoundary(window.source_start + timedelta(seconds=1), window.source_end)
    assert (
        reduce_findings(
            boundary=boundary(event("event.start", "task.one", window.source_start), start=start)
        )
        == ()
    )


def test_global_latest_version_is_selected_before_window_membership() -> None:
    start = datetime(2026, 3, 25, tzinfo=LONDON)
    window = boundary(start=start)
    rows = reduce_findings(
        boundary=boundary(
            event("event.one", "task.one", window.source_start + timedelta(days=1), 1),
            event("event.one", "task.one", window.source_end + timedelta(seconds=1), 2),
            start=start,
        )
    )
    assert rows == ()


def test_immutable_finding_identity_is_deterministic_and_bound_to_evidence_window() -> None:
    start = datetime(2026, 3, 25, tzinfo=LONDON)
    first = reduce_findings(
        boundary=boundary(
            event("event.one", "task.one", start + timedelta(days=1)),
            start=start,
        )
    )[0]
    repeated = reduce_findings(
        boundary=boundary(
            event("event.one", "task.one", start + timedelta(days=1)),
            start=start,
        )
    )[0]
    shifted_start = start + timedelta(days=7)
    shifted = reduce_findings(
        boundary=boundary(
            event("event.one", "task.one", shifted_start + timedelta(days=1)),
            start=shifted_start,
        )
    )[0]
    assert first.immutable_id == repeated.immutable_id
    assert first.version == 1
    assert first.immutable_id != shifted.immutable_id


def test_detector_versioned_identity_prevents_cross_detector_promotion() -> None:
    start = datetime(2026, 3, 25, tzinfo=LONDON)
    rows = reduce_findings(
        boundary=boundary(
            event("event.one", "task.one", start + timedelta(days=1), detector="detector.one"),
            event("event.two", "task.two", start + timedelta(days=2), detector="detector.two"),
            start=start,
        )
    )
    assert len(rows) == 2
    assert all(not row.actionable for row in rows)


def test_duplicate_event_version_identity_fails_closed() -> None:
    start = datetime(2026, 3, 25, tzinfo=LONDON)
    with pytest.raises(PrototypeContractError, match="duplicate"):
        reduce_findings(
            boundary=boundary(
                event("event.one", "task.one", start + timedelta(days=1)),
                event("event.one", "task.two", start + timedelta(days=2)),
                start=start,
            )
        )


def test_candidate_rejects_impossible_counts() -> None:
    with pytest.raises(PrototypeContractError, match="bounded"):
        FindingCandidate(
            "detector",
            1,
            FINGERPRINT,
            1,
            2,
            1,
            ("event.one",),
            (1,),
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 8, tzinfo=UTC),
        )


@pytest.mark.parametrize("count", [True, 1.0, "1"])
def test_candidate_rejects_noninteger_aggregate_counts(count: object) -> None:
    with pytest.raises(PrototypeContractError, match="integers"):
        FindingCandidate(
            "detector",
            1,
            FINGERPRINT,
            count,  # type: ignore[arg-type]
            1,
            1,
            ("event.one",),
            (1,),
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 8, tzinfo=UTC),
        )

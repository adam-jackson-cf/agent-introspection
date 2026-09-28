import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from experiments.dashboard_prototype.recurrence_finding_capture import (
    RecurrenceFindingScanObservation,
    capture_callback,
    new_capture_path,
    persist,
)

HASH = "a" * 64


def observation(
    *,
    state: str = "qualified",
    task: str | None = "thread:task-1",
    window: tuple[int | None, int | None] = (None, None),
) -> RecurrenceFindingScanObservation:
    return RecurrenceFindingScanObservation(
        scan_run_id="scan-1",
        observed_at=datetime(2026, 3, 30, 12, tzinfo=UTC),
        evidence_start_ns=window[0],
        evidence_end_ns=window[1],
        evidence_window_definition="rolling_utc_7d" if window[0] is not None else None,
        finding_version_source_id=HASH,
        membership_source_id="b" * 64,
        activity_version_source_id="c" * 64,
        activity_source_started_at_ns=0,
        activity_source_ended_at_ns=1,
        detector_id="tool-loop",
        detector_version=1,
        finding_fingerprint="d" * 64,
        finding_state="emerging",
        finding_occurrence_count=1,
        finding_canonical_task_count=1,
        finding_local_day_count=1,
        task_membership_state=state,
        native_task_id=task,
        producer="codex-cli",
        activity_version=1,
        latest_activity_version=1,
    )


@pytest.mark.parametrize(
    "window", [(None, None), (1_774_267_200_000_000_123, 1_774_872_000_000_000_123)]
)
def test_persist_keeps_observed_bounds_and_cannot_rewrite_membership(
    tmp_path: Path, window: tuple[int | None, int | None]
) -> None:
    path = new_capture_path(tmp_path, now=datetime(2026, 3, 30, tzinfo=UTC))
    persist(path, (observation(window=window),))

    connection = sqlite3.connect(path)
    try:
        assert (
            connection.execute(
                "SELECT evidence_start_ns, evidence_end_ns FROM finding_membership_observation"
            ).fetchone()
            == window
        )
        assert connection.execute(
            "SELECT task_membership_state, native_task_id FROM finding_membership_observation"
        ).fetchone() == ("qualified", "thread:task-1")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE finding_membership_observation SET evidence_end_ns = 0")
    finally:
        connection.close()


def test_persist_rejects_existing_capture_path(tmp_path: Path) -> None:
    path = new_capture_path(tmp_path)
    persist(path, (observation(),))

    with pytest.raises(FileExistsError):
        persist(path, (observation(),))


def test_empty_scanner_observation_is_a_non_synthetic_capture_noop(tmp_path: Path) -> None:
    path = new_capture_path(tmp_path)

    capture_callback(path)(())

    assert not path.exists()


@pytest.mark.parametrize(
    ("state", "task", "error"),
    [
        ("episode_task_identity", "thread:native-task", "qualified membership"),
        ("qualified", None, "qualified membership"),
        ("qualified", "thread:", "exact thread identity"),
        ("qualified", HASH, "exact thread identity"),
    ],
)
def test_unqualified_membership_has_no_task_identity(
    state: str, task: str | None, error: str
) -> None:
    with pytest.raises(ValueError, match=error):
        observation(state=state, task=task)

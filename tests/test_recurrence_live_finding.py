import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from agent_introspection.scan import RecurrenceFindingScanObservation
from experiments.dashboard_prototype.contracts import ExperimentResult
from experiments.dashboard_prototype.experiment_live_common import LiveProofRequest
from experiments.dashboard_prototype.recurrence_finding_capture import persist
from experiments.dashboard_prototype.recurrence_live_finding import extract_capture

HASH = "a" * 64


def request() -> LiveProofRequest:
    start = datetime(2026, 3, 25, tzinfo=ZoneInfo("Europe/London"))
    end = datetime(2026, 4, 1, tzinfo=ZoneInfo("Europe/London"))
    return LiveProofRequest("recurrence.live.1", start, end)


def observation(
    *,
    window: tuple[int | None, int | None] | None = None,
    producer: str = "codex-cli",
    occurrence_count: int = 1,
    latest_activity_version: int = 1,
    window_definition: str | None = "europe_london_calendar_7d",
) -> RecurrenceFindingScanObservation:
    proof_request = request()
    start_ns = int(proof_request.start.timestamp() * 1_000_000_000)
    end_ns = int(proof_request.end.timestamp() * 1_000_000_000)
    return RecurrenceFindingScanObservation(
        scan_run_id="scan-1",
        observed_at=datetime(2026, 4, 1, tzinfo=UTC),
        evidence_start_ns=start_ns if window is None else window[0],
        evidence_end_ns=end_ns if window is None else window[1],
        evidence_window_definition=window_definition if window is None else None,
        finding_version_source_id=HASH,
        membership_source_id="b" * 64,
        activity_version_source_id="c" * 64,
        activity_source_started_at_ns=start_ns + 1,
        activity_source_ended_at_ns=start_ns + 2,
        detector_id="tool-loop",
        detector_version=1,
        finding_fingerprint="d" * 64,
        finding_state="emerging",
        finding_occurrence_count=occurrence_count,
        finding_canonical_task_count=1,
        finding_local_day_count=1,
        task_membership_state="qualified",
        native_task_id="thread:task-1",
        producer=producer,
        activity_version=1,
        latest_activity_version=latest_activity_version,
    )


def capture(path: Path, *rows: RecurrenceFindingScanObservation) -> Path:
    persist(path, rows)
    return path


def test_committed_capture_preserves_native_latest_version_authority(tmp_path: Path) -> None:
    evidence = extract_capture(capture(tmp_path / "finding.sqlite", observation()), request())

    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.assertions["membership_task_identity_authoritative"] is True
    assert evidence.proof.assertions["latest_activity_versions_global"] is True
    assert (
        "missing_global_latest_activity_version_authority" not in evidence.proof.blocked_boundaries
    )
    assert len(evidence.primitives) == 3
    assert all(primitive.measures == {"reducer_counts": 1.0} for primitive in evidence.primitives)


def test_stale_captured_activity_version_blocks_global_latest_authority(tmp_path: Path) -> None:
    evidence = extract_capture(
        capture(
            tmp_path / "finding.sqlite",
            observation(latest_activity_version=2),
        ),
        request(),
    )

    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.assertions["latest_activity_versions_global"] is False
    assert "missing_global_latest_activity_version_authority" in evidence.proof.blocked_boundaries


def test_missing_window_bounds_stays_blocked(tmp_path: Path) -> None:
    evidence = extract_capture(
        capture(tmp_path / "finding.sqlite", observation(window=(None, None))), request()
    )

    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.assertions["durable_window_bounds_authoritative"] is False
    assert "missing_immutable_evidence_window" in evidence.proof.blocked_boundaries


def test_rolling_utc_window_cannot_claim_calendar_authority(tmp_path: Path) -> None:
    evidence = extract_capture(
        capture(tmp_path / "finding.sqlite", observation(window_definition="rolling_utc_7d")),
        request(),
    )

    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.assertions["durable_window_bounds_authoritative"] is False


def test_counter_contradiction_fails_without_emitting_tautological_aggregate(
    tmp_path: Path,
) -> None:
    evidence = extract_capture(
        capture(tmp_path / "finding.sqlite", observation(occurrence_count=2)), request()
    )

    assert evidence.proof.result is ExperimentResult.FAILED
    assert evidence.proof.assertions["contradiction_detected"] is True
    assert evidence.primitives == ()


def test_unsupported_producer_cannot_supply_task_membership_authority(tmp_path: Path) -> None:
    evidence = extract_capture(
        capture(tmp_path / "finding.sqlite", observation(producer="claude")), request()
    )

    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.assertions["membership_task_identity_authoritative"] is False


@pytest.mark.parametrize("mutation", ["missing_table", "counter_type", "trigger_body"])
def test_old_capture_schema_is_not_reinterpreted(tmp_path: Path, mutation: str) -> None:
    path = tmp_path / "old.sqlite"
    if mutation != "missing_table":
        capture(path, observation())
    connection = sqlite3.connect(path)
    try:
        if mutation == "missing_table":
            connection.execute("CREATE TABLE capture (scan_run_id TEXT, observed_at_ns INTEGER)")
        elif mutation == "counter_type":
            connection.execute("PRAGMA writable_schema=ON")
            connection.execute(
                "UPDATE sqlite_master SET sql = replace(sql, "
                "'finding_occurrence_count INTEGER NOT NULL', "
                "'finding_occurrence_count REAL NOT NULL') "
                "WHERE type = 'table' AND name = 'finding_membership_observation'"
            )
        else:
            connection.execute("DROP TRIGGER finding_membership_observation_no_update")
            connection.execute(
                "CREATE TRIGGER finding_membership_observation_no_update "
                "BEFORE UPDATE ON finding_membership_observation BEGIN SELECT 1; END"
            )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ValueError, match="schema"):
        extract_capture(path, request())

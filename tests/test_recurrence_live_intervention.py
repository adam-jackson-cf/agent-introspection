from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from experiments.dashboard_prototype.contracts import ExperimentResult
from experiments.dashboard_prototype.experiment_live_common import LiveProofRequest
from experiments.dashboard_prototype.recurrence_common import (
    RecurrenceLiveEvidence,
    RecurrencePrimitive,
)
from experiments.dashboard_prototype.recurrence_live_intervention import extract

START = datetime(2026, 9, 2, 12, tzinfo=UTC)
REQUEST = LiveProofRequest("recurrence-live-4", START, START + timedelta(minutes=5))


def _utc_epoch_ns(value: datetime) -> int:
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE findings (last_seen_ns INTEGER)")
    for table in (
        "proposals",
        "proposal_events",
        "proposal_drafts",
        "semantic_classifications",
    ):
        connection.execute(f"CREATE TABLE {table} (created_at TEXT)")
    return connection


def _primitive(evidence: RecurrenceLiveEvidence, stable_identity: str) -> RecurrencePrimitive:
    return next(
        primitive
        for primitive in evidence.primitives
        if primitive.dimensions["stable_identity"] == stable_identity
    )


def test_six_live_findings_use_last_seen_window_not_population_labels() -> None:
    connection = _connection()
    start_ns = _utc_epoch_ns(REQUEST.start)
    end_ns = _utc_epoch_ns(REQUEST.end)
    connection.executemany(
        "INSERT INTO findings VALUES (?)",
        ((start_ns,), *((end_ns,) for _ in range(6))),
    )

    evidence = extract(connection, REQUEST)
    findings = _primitive(evidence, "findings")

    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.metrics["finding_row_count"] == 6
    assert findings.measures["reducer_counts"] == 6
    assert evidence.proof.evidence_ids
    assert findings.dimensions["capability_state"] == "auditable"


def test_findings_use_exact_open_closed_integer_epoch_bounds() -> None:
    connection = _connection()
    start_ns = _utc_epoch_ns(REQUEST.start)
    end_ns = _utc_epoch_ns(REQUEST.end)
    connection.executemany(
        "INSERT INTO findings VALUES (?)",
        (
            (start_ns,),
            (start_ns + 1,),
            (end_ns,),
            (end_ns + 1,),
        ),
    )

    findings = _primitive(extract(connection, REQUEST), "findings")

    assert findings.measures["reducer_counts"] == 2


def test_empty_production_tables_remain_blocked_and_future_projections_absent() -> None:
    evidence = extract(_connection(), REQUEST)

    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert "missing_durable_authority" in evidence.proof.blocked_boundaries
    assert "canonical_practices" in evidence.proof.blocked_boundaries
    assert evidence.proof.assertions["workflow_is_full_projection"] is False
    assert len(evidence.primitives) == 9
    assert all(row.measures["reducer_counts"] == 0 for row in evidence.primitives)
    assert all(
        set(row.dimensions) <= {"stable_identity", "capability_state"}
        and set(row.measures) == {"reducer_counts"}
        for row in evidence.primitives
    )


def test_canonical_count_uses_latest_versions_and_excludes_unsupported_producers() -> None:
    connection = _connection()
    connection.execute(
        """
        CREATE TABLE canonical_activities (
            id TEXT,
            producer TEXT,
            producer_surface TEXT,
            source_ended_at_ns INTEGER
        )
        """
    )
    connection.execute(
        "CREATE TABLE canonical_activity_versions (activity_id TEXT, version INTEGER)"
    )
    end_ns = _utc_epoch_ns(REQUEST.end)
    connection.executemany(
        "INSERT INTO canonical_activities VALUES (?, ?, ?, ?)",
        (
            ("included", "omp", "omp", end_ns),
            ("unsupported", "claude-code", "claude-code", end_ns),
            ("wrong_surface", "omp", "other", end_ns),
        ),
    )
    connection.executemany(
        "INSERT INTO canonical_activity_versions VALUES (?, ?)",
        (("included", 1), ("included", 2), ("unsupported", 1), ("wrong_surface", 1)),
    )

    evidence = extract(connection, REQUEST)
    canonical = _primitive(evidence, "canonical_activities")

    assert canonical.measures["reducer_counts"] == 1
    assert canonical.dimensions["capability_state"] == "auditable"

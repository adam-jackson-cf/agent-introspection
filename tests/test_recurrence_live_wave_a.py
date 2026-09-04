from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from experiments.dashboard_prototype.contracts import ExperimentResult
from experiments.dashboard_prototype.experiment_live_common import LiveProofRequest
from experiments.dashboard_prototype.recurrence_live_wave_a import extract

START = datetime(2026, 9, 2, 12, tzinfo=UTC)
END = START + timedelta(minutes=5)

FROZEN_PRIVACY_ALLOWLIST = frozenset(
    json.loads(Path("docs/dashboard-prototype-proof-matrix.json").read_text())["privacy_allowlist"]
)


def _request() -> LiveProofRequest:
    return LiveProofRequest("run-1", START, END)


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE canonical_activities (
          id TEXT PRIMARY KEY, producer TEXT, producer_surface TEXT,
          correlation_id TEXT, source_ended_at_ns INTEGER, detector_id TEXT,
          operation_kind TEXT, target_kind TEXT, normalized_target TEXT,
          normalized_failure_class TEXT
        );
        CREATE TABLE canonical_activity_versions (
          activity_id TEXT, version INTEGER, attribution_state TEXT,
          project_identity_id TEXT
        );
        """
    )
    return connection


def _insert(
    connection: sqlite3.Connection,
    activity_id: str,
    producer: str = "omp",
    surface: str = "omp",
    ended: datetime = START + timedelta(seconds=1),
) -> None:
    connection.execute(
        "INSERT INTO canonical_activities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            activity_id,
            producer,
            surface,
            "correlation-not-a-task",
            int(ended.timestamp() * 1_000_000_000),
            "detector",
            "operation",
            "target",
            "/private/unsafe/project",
            "failure",
        ),
    )


def test_live_extractor_selects_latest_versions_and_all_supported_routes() -> None:
    connection = _connection()
    _insert(connection, "a", "omp", "omp")
    _insert(connection, "b", "codex-cli", "codex-cli")
    _insert(connection, "c", "codex-app-server", "codex-app-server")
    for activity_id, project in (("a", "old"), ("b", "project-b"), ("c", "project-c")):
        connection.execute(
            "INSERT INTO canonical_activity_versions VALUES (?, 1, 'resolved', ?)",
            (activity_id, project),
        )
    connection.execute(
        "INSERT INTO canonical_activity_versions VALUES ('a', 2, 'unresolved', NULL)"
    )

    evidence = extract(connection, _request())
    m16 = [row for row in evidence.primitives if row.dimensions["policy_identity"] == "m16"]
    m13 = [row for row in evidence.primitives if row.dimensions["policy_identity"] == "m13"]

    assert len(m16) == 3
    assert sum(row.measures["reducer_counts"] for row in m16) == 3
    assert {row.dimensions["producer"] for row in m16} == {"omp", "codex-cli", "codex-app-server"}
    assert m16[0].dimensions["capability_state"] == "unresolved"
    assert len(m13) == 3
    assert evidence.proof.result is ExperimentResult.BLOCKED


def test_tied_latest_version_fails_without_m16_calculation() -> None:
    connection = _connection()
    _insert(connection, "a")
    connection.executemany(
        "INSERT INTO canonical_activity_versions VALUES ('a', 2, ?, ?)",
        (("resolved", "project-a"), ("unresolved", None)),
    )

    evidence = extract(connection, _request())

    assert evidence.proof.result is ExperimentResult.FAILED
    assert evidence.proof.assertions["contradiction_detected"] is True
    assert evidence.proof.blocked_boundaries == ("ambiguous_latest_activity_version",)
    assert evidence.primitives == ()
    assert "m16_total_count" not in evidence.proof.metrics


def test_older_duplicate_does_not_block_unique_latest_version() -> None:
    connection = _connection()
    _insert(connection, "a")
    connection.executemany(
        "INSERT INTO canonical_activity_versions VALUES ('a', ?, 'unresolved', NULL)",
        ((1,), (1,), (2,)),
    )

    evidence = extract(connection, _request())

    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert (
        sum(
            row.measures.get("reducer_counts", 0)
            for row in evidence.primitives
            if row.dimensions["policy_identity"] == "m16"
        )
        == 1
    )


def test_unique_latest_version_remains_selectable() -> None:
    connection = _connection()
    _insert(connection, "a")
    connection.execute(
        "INSERT INTO canonical_activity_versions VALUES ('a', 1, 'unresolved', NULL)"
    )

    evidence = extract(connection, _request())

    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert (
        sum(
            row.measures.get("reducer_counts", 0)
            for row in evidence.primitives
            if row.dimensions["policy_identity"] == "m16"
        )
        == 1
    )


def test_empty_route_is_ambiguous_not_inferred_zero_or_task_identity() -> None:
    evidence = extract(_connection(), _request())

    m13 = [row for row in evidence.primitives if row.dimensions["policy_identity"] == "m13"]
    assert {row.dimensions["capability_state"] for row in m13} == {"ambiguous"}
    assert evidence.proof.metrics["m13_ambiguous_route_count"] == 3


def test_live_extractor_hides_correlation_and_sensitive_source_values() -> None:
    connection = _connection()
    _insert(connection, "a")
    connection.execute(
        "INSERT INTO canonical_activity_versions VALUES ('a', 1, 'resolved', 'project-path')"
    )

    evidence = extract(connection, _request())
    rendered = evidence.proof.canonical_json() + repr(evidence.primitives)

    assert "correlation-not-a-task" not in rendered
    assert "/private/unsafe/project" not in rendered
    assert "project-path" not in rendered
    assert all("correlation" not in row.dimensions for row in evidence.primitives)
    assert all(
        set(row.dimensions) <= FROZEN_PRIVACY_ALLOWLIST
        and set(row.measures) <= FROZEN_PRIVACY_ALLOWLIST
        for row in evidence.primitives
    )


def test_source_endpoints_use_open_closed_membership() -> None:
    connection = _connection()
    _insert(connection, "start", ended=START)
    _insert(connection, "end", ended=END)
    for activity_id in ("start", "end"):
        connection.execute(
            "INSERT INTO canonical_activity_versions VALUES (?, 1, 'unresolved', NULL)",
            (activity_id,),
        )

    evidence = extract(connection, _request())

    assert (
        sum(
            row.measures.get("reducer_counts", 0)
            for row in evidence.primitives
            if row.dimensions["policy_identity"] == "m16"
        )
        == 1
    )


def test_one_nanosecond_after_start_is_preserved_by_integer_query_membership() -> None:
    connection = _connection()
    _insert(connection, "near-start", ended=START)
    connection.execute(
        "UPDATE canonical_activities SET source_ended_at_ns = source_ended_at_ns + 1 "
        "WHERE id = 'near-start'"
    )
    connection.execute(
        "INSERT INTO canonical_activity_versions VALUES ('near-start', 1, 'unresolved', NULL)"
    )

    evidence = extract(connection, _request())
    activity = next(
        row for row in evidence.primitives if row.dimensions["policy_identity"] == "m16"
    )

    expected_source_time_ns = int(START.timestamp() * 1_000_000_000) + 1
    assert activity.source_time == START
    assert activity.source_time_ns == expected_source_time_ns
    assert activity.dimensions["timestamp_ns"] == expected_source_time_ns
    assert evidence.proof.assertions["m16_source_time_ns_bound"] is True

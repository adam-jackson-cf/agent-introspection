import sqlite3
from datetime import UTC, datetime, timedelta

from experiments.dashboard_prototype.contracts import ExperimentResult
from experiments.dashboard_prototype.pipeline_live_common import LiveProofRequest
from experiments.dashboard_prototype.pipeline_live_terminal_cadence import extract

T0 = datetime(2026, 9, 1, tzinfo=UTC)


def request() -> LiveProofRequest:
    return LiveProofRequest("live-run", T0, T0 + timedelta(minutes=15))


def schedule_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE scan_runs (
            id TEXT, status TEXT, completed_at TEXT, scheduled_at TEXT,
            schedule_policy_identity TEXT, schedule_interval_seconds INTEGER,
            schedule_timezone TEXT, schedule_anchor_at TEXT
        )
        """
    )
    return connection


def insert_run(connection: sqlite3.Connection, identity: str, status: str, minute: int) -> None:
    instant = T0 + timedelta(minutes=minute)
    connection.execute(
        """
        INSERT INTO scan_runs VALUES (?, ?, ?, ?, 'durable-schedule-v1', 300, 'UTC', ?)
        """,
        (identity, status, instant.isoformat(), instant.isoformat(), T0.isoformat()),
    )


def test_current_scan_run_shape_blocks_without_durable_schedule_identity() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE scan_runs (
            id TEXT PRIMARY KEY, status TEXT NOT NULL, started_at TEXT NOT NULL,
            completed_at TEXT, source_start_ns INTEGER, source_end_ns INTEGER,
            rows_processed INTEGER NOT NULL DEFAULT 0, error_code TEXT,
            details_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    connection.execute(
        "INSERT INTO scan_runs (id, status, started_at, completed_at) VALUES (?, ?, ?, ?)",
        ("real-success", "succeeded", T0.isoformat(), (T0 + timedelta(minutes=5)).isoformat()),
    )

    evidence = extract(connection, request())

    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.blocked_boundaries == ("authoritative schedule policy identity",)
    assert evidence.primitives == ()
    assert evidence.remote_query_id is None


def test_durable_terminal_statuses_and_latest_success_are_reduced() -> None:
    connection = schedule_connection()
    insert_run(connection, "success-old", "succeeded", 5)
    insert_run(connection, "failure-recent", "failed", 10)
    insert_run(connection, "empty-recent", "no_data", 15)

    evidence = extract(connection, request())

    assert evidence.proof.result is ExperimentResult.FAILED
    assert evidence.proof.metrics == {
        "succeeded_count": 1,
        "failed_count": 1,
        "no_data_count": 1,
        "observed_count": 3,
        "expected_slot_count": 3,
        "missed_cadence_count": 1,
        "freshness_seconds": 600,
        "stale_boundary": (T0 + timedelta(minutes=10)).isoformat(),
        "is_stale": True,
    }


def test_proven_primitives_preserve_complete_typed_cadence_authority() -> None:
    connection = schedule_connection()
    insert_run(connection, "first", "failed", 5)
    insert_run(connection, "second", "no_data", 10)
    insert_run(connection, "third", "succeeded", 15)

    evidence = extract(connection, request())

    assert evidence.proof.result is ExperimentResult.PROVEN
    assert evidence.remote_query_id is not None
    assert [row.source_time for row in evidence.primitives] == [
        T0 + timedelta(minutes=5),
        T0 + timedelta(minutes=10),
        T0 + timedelta(minutes=15),
    ]
    assert [row.dimensions["terminal_state"] for row in evidence.primitives] == [
        "failed",
        "no_data",
        "succeeded",
    ]
    assert all(
        row.dimensions
        == {
            "durable_id_hash": row.dimensions["durable_id_hash"],
            "terminal_state": row.dimensions["terminal_state"],
            "schedule_policy_identity": "durable-schedule-v1",
            "schedule_timezone": "UTC",
            "schedule_anchor_at": T0.isoformat().replace("+00:00", "Z"),
            "scheduled_at": row.source_time.isoformat().replace("+00:00", "Z"),
            "window_start": T0.isoformat().replace("+00:00", "Z"),
            "window_end": (T0 + timedelta(minutes=15)).isoformat().replace("+00:00", "Z"),
        }
        and row.measures == {"schedule_interval_seconds": 300, "scheduled_count": 1}
        and isinstance(row.dimensions["durable_id_hash"], str)
        and len(row.dimensions["durable_id_hash"]) == 64
        for row in evidence.primitives
    )


def test_unclassified_terminal_state_emits_authority_but_remains_blocked() -> None:
    connection = schedule_connection()
    insert_run(connection, "unclassified", "cancelled", 10)

    evidence = extract(connection, request())

    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.blocked_boundaries == ("unsupported durable terminal state:cancelled",)
    assert evidence.remote_query_id is not None
    assert len(evidence.primitives) == 1
    assert evidence.primitives[0].dimensions["terminal_state"] == "cancelled"
    assert evidence.primitives[0].dimensions["scheduled_at"] == (
        T0 + timedelta(minutes=10)
    ).isoformat().replace("+00:00", "Z")


def test_completed_running_row_fails_closed_in_terminal_population() -> None:
    connection = schedule_connection()
    insert_run(connection, "running-row", "running", 10)

    evidence = extract(connection, request())

    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.blocked_boundaries == ("unsupported durable terminal state:running",)


def test_order_is_deterministic_and_proven_inputs_are_hashed() -> None:
    first = schedule_connection()
    second = schedule_connection()
    for connection, rows in (
        (first, (("z-run", "failed", 5), ("a-run", "succeeded", 15))),
        (second, (("a-run", "succeeded", 15), ("z-run", "failed", 5))),
    ):
        for identity, status, minute in rows:
            insert_run(connection, identity, status, minute)

    first_evidence = extract(first, request())
    second_evidence = extract(second, request())

    assert first_evidence.proof.canonical_json() == second_evidence.proof.canonical_json()
    assert first_evidence.proof.result is ExperimentResult.PROVEN
    assert first_evidence.remote_query_id == second_evidence.remote_query_id
    assert all(
        isinstance(row.dimensions["durable_id_hash"], str)
        and len(row.dimensions["durable_id_hash"]) == 64
        for row in first_evidence.primitives
    )

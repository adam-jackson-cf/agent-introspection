from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from experiments.dashboard_prototype.contracts import ExperimentResult
from experiments.dashboard_prototype.pipeline_live_common import LiveProofRequest
from experiments.dashboard_prototype.pipeline_live_snapshot import extract

START = datetime(2026, 9, 1, 12, tzinfo=UTC)
END = START + timedelta(minutes=10)


def _database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE scan_runs (
            id TEXT, status TEXT, completed_at TEXT, rows_processed INTEGER
        );
        CREATE TABLE native_pipeline_snapshots (
            event_id TEXT PRIMARY KEY, scan_run_id TEXT NOT NULL
        );
        CREATE TABLE scan_execution_inputs (
            scan_run_id TEXT PRIMARY KEY,
            monotonic_started REAL, monotonic_finished REAL,
            logs_json TEXT, traces_json TEXT,
            context_events_json TEXT, canonical_activities_json TEXT
        );
        """
    )
    connection.execute(
        "INSERT INTO scan_runs VALUES ('scan-1', 'succeeded', ?, 999)", (END.isoformat(),)
    )
    connection.execute(
        "INSERT INTO native_pipeline_snapshots VALUES ('native-snapshot-1', 'scan-1')"
    )
    connection.execute(
        """INSERT INTO scan_execution_inputs VALUES
           ('scan-1', 100.0, 100.2505, '["log-1","log-1","log-2"]', '["trace-1"]', '[]', '[]')"""
    )
    return connection


def _request() -> LiveProofRequest:
    return LiveProofRequest("run-1", START, END)


def test_native_snapshot_retains_float_duration_and_exact_scan_bound_count() -> None:
    connection = _database()
    evidence = extract(connection, _request())

    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.metrics["observed_native_snapshot_count"] == 1
    assert evidence.proof.metrics["observed_native_duration_ms"] == pytest.approx(250.5)
    assert evidence.proof.metrics["observed_native_rows_processed"] == 4

    connection.execute("UPDATE scan_execution_inputs SET logs_json = NULL")
    unknown = extract(connection, _request())
    assert unknown.proof.metrics["observed_native_rows_processed"] is None

    connection.execute("UPDATE scan_execution_inputs SET logs_json = '[]', traces_json = '[]'")
    empty = extract(connection, _request())
    assert empty.proof.metrics["observed_native_rows_processed"] == 0

    connection.execute("UPDATE scan_execution_inputs SET logs_json = '{}'")
    with pytest.raises(ValueError, match="source identities"):
        extract(connection, _request())


def test_native_snapshot_must_bind_to_a_selected_exact_scan_id() -> None:
    connection = _database()
    connection.execute(
        "INSERT INTO native_pipeline_snapshots VALUES ('unbound-native-snapshot', 'other-scan')"
    )

    evidence = extract(connection, _request())

    assert evidence.proof.metrics["observed_native_snapshot_count"] == 1
    assert evidence.proof.metrics["observed_native_rows_processed"] == 4

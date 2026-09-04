from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import cast

from experiments.dashboard_prototype.contracts import ExperimentResult
from experiments.dashboard_prototype.pipeline_live_common import LiveProofRequest
from experiments.dashboard_prototype.pipeline_live_snapshot import extract
from experiments.dashboard_prototype.pipeline_snapshot import (
    PipelineSnapshotAuthorityRecord,
    ScanErrorClass,
    ScanTerminalClass,
    SnapshotPopulationCounts,
)

START = datetime(2026, 9, 1, 12, tzinfo=UTC)
END = START + timedelta(minutes=10)


def _database(**options: str | int | bool) -> sqlite3.Connection:
    duplicate_snapshot = bool(options.get("duplicate_snapshot", False))
    scan_scoped_populations = bool(options.get("scan_scoped_populations", True))
    omit_schema = bool(options.get("omit_schema", False))
    omit_field = options.get("omit_field")
    scan_status = str(options.get("scan_status", "succeeded"))
    payload_terminal = str(options.get("payload_terminal", "completed"))
    payload_error = str(options.get("payload_error", "none"))
    include_attempt_authority = bool(options.get("include_attempt_authority", True))
    immutable_pending = int(options.get("immutable_pending", 1))
    immutable_failed = int(options.get("immutable_failed", 1))
    current_pending = int(options.get("current_pending", 1))
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE scan_runs (
            id TEXT, status TEXT, completed_at TEXT, rows_processed INTEGER
        );
        CREATE TABLE source_session_records (scan_run_id TEXT, source_kind TEXT);
        CREATE TABLE canonical_activities (
            created_at TEXT"""
        + (", scan_run_id TEXT" if scan_scoped_populations else "")
        + """
        );
        CREATE TABLE session_context_events (
            occurred_at TEXT"""
        + (", scan_run_id TEXT" if scan_scoped_populations else "")
        + """
        );
        CREATE TABLE otlp_outbox (event_id TEXT, payload_json TEXT, status TEXT);
        """
        + (
            """
            CREATE TABLE outbox_drain_attempts (
                drain_id TEXT,
                pending_outbox_count INTEGER,
                failed_during_drain_count INTEGER
            );
            """
            if include_attempt_authority
            else ""
        )
    )
    completed = END.isoformat()
    rows_processed = None if options.get("omit_field") == "rows" else 4
    connection.execute(
        "INSERT INTO scan_runs VALUES ('scan-1', ?, ?, ?)",
        (scan_status, completed, rows_processed),
    )
    connection.executemany(
        "INSERT INTO source_session_records VALUES ('scan-1', ?)", [("log",), ("trace",)]
    )
    if scan_scoped_populations:
        connection.execute("INSERT INTO canonical_activities VALUES (?, 'scan-1')", (completed,))
        connection.execute("INSERT INTO session_context_events VALUES (?, 'scan-1')", (completed,))
    else:
        connection.execute("INSERT INTO canonical_activities VALUES (?)", (completed,))
        connection.execute("INSERT INTO session_context_events VALUES (?)", (completed,))
    if include_attempt_authority:
        connection.execute(
            "INSERT INTO outbox_drain_attempts VALUES ('drain-1', ?, ?)",
            (immutable_pending, immutable_failed),
        )
    payload = {
        "event.name": "dashboard.pipeline.snapshot",
        "entity.id": "scan-1",
        "pipeline.payload_schema_version": 1,
        "scan.terminal_class": payload_terminal,
        "scan.completed_at_ns": 1_788_264_600_000_000_000,
        "scan.duration_ms": 250,
        "scan.error_class": payload_error,
        "logs.count": 1,
        "traces.count": 1,
        "context_events.count": 1,
        "canonical_activities.count": 1,
        "source_sessions.count": 2,
        "outbox.pending.count": immutable_pending,
        "outbox.failed_during_drain.count": immutable_failed,
        "pipeline.bounded_drain_id": "drain-1",
    }
    if omit_schema:
        del payload["pipeline.payload_schema_version"]
    if isinstance(omit_field, str):
        payload.pop(omit_field, None)
    event_id = None if omit_field == "event_id" else "snapshot-1"
    connection.execute(
        "INSERT INTO otlp_outbox VALUES (?, ?, 'delivered')", (event_id, json.dumps(payload))
    )
    for index in range(current_pending):
        connection.execute(
            "INSERT INTO otlp_outbox VALUES (?, '{}', 'pending')", (f"pending-{index}",)
        )
    if duplicate_snapshot:
        payload["scan.duration_ms"] = 251
        connection.execute(
            "INSERT INTO otlp_outbox VALUES ('snapshot-2', ?, 'delivered')", (json.dumps(payload),)
        )
    return connection


def _request() -> LiveProofRequest:
    return LiveProofRequest("run-1", START, END)


def test_extracts_current_snapshot_and_preserves_row_workload() -> None:
    connection = _database(scan_scoped_populations=True)
    evidence = extract(connection, _request())
    assert evidence.proof.result is ExperimentResult.PROVEN
    assert evidence.proof.metrics["latest_payload_schema_version"] == 1
    assert evidence.proof.metrics["latest_terminal_class"] == "completed"
    assert evidence.proof.metrics["latest_error_class"] == "none"
    assert evidence.proof.metrics["latest_bounded_drain_id"] == (
        "8a1d6d3e2934b11859cac3cfacc8d3bcb80f6a8428d06c7bc8b86c6cb6fc9190"
    )
    assert evidence.proof.metrics["latest_workload_count"] == 4
    assert {
        name: evidence.proof.metrics[name]
        for name in (
            "latest_rows",
            "latest_logs",
            "latest_traces",
            "latest_context_events",
            "latest_canonical_activities",
            "latest_source_sessions",
            "latest_pending_outbox",
            "latest_failed_during_drain_attempt_events",
        )
    } == {
        "latest_rows": 4,
        "latest_logs": 1,
        "latest_traces": 1,
        "latest_context_events": 1,
        "latest_canonical_activities": 1,
        "latest_source_sessions": 2,
        "latest_pending_outbox": 1,
        "latest_failed_during_drain_attempt_events": 1,
    }
    assert evidence.proof.metrics["latest_duration_ms"] == 250
    assert evidence.remote_query_id == "snapshot-run-1"
    primitive = evidence.primitives[0]
    assert primitive.measures == {
        "completed_at_ns": 1_788_264_600_000_000_000,
        "payload_schema_version": 1,
        "duration_ms": 250,
        "rows": 4,
        "logs": 1,
        "traces": 1,
        "context_events": 1,
        "canonical_activities": 1,
        "source_sessions": 2,
        "pending_outbox": 1,
        "failed_during_drain": 1,
    }
    dimensions = primitive.dimensions
    measures = primitive.measures
    event_id = cast(str, dimensions["event_id"])
    terminal_class = cast(str, dimensions["terminal_class"])
    error_class = cast(str, dimensions["error_class"])
    bounded_drain_id = cast(str, dimensions["bounded_drain_id"])
    assert all(
        isinstance(value, str)
        for value in (event_id, terminal_class, error_class, bounded_drain_id)
    )
    integer_measures = {name: cast(int, value) for name, value in measures.items()}
    assert all(
        isinstance(value, int) and not isinstance(value, bool)
        for value in integer_measures.values()
    )
    authority = PipelineSnapshotAuthorityRecord(
        event_id=event_id,
        completed_at_ns=integer_measures["completed_at_ns"],
        payload_schema_version=integer_measures["payload_schema_version"],
        terminal_class=ScanTerminalClass(terminal_class),
        duration_ms=integer_measures["duration_ms"],
        error_class=ScanErrorClass(error_class),
        counts=SnapshotPopulationCounts(
            rows=integer_measures["rows"],
            logs=integer_measures["logs"],
            traces=integer_measures["traces"],
            context_events=integer_measures["context_events"],
            canonical_activities=integer_measures["canonical_activities"],
            source_sessions=integer_measures["source_sessions"],
            pending_outbox=integer_measures["pending_outbox"],
            failed_during_drain=integer_measures["failed_during_drain"],
        ),
        bounded_drain_id=bounded_drain_id,
    )
    authority.validate()
    assert primitive.dimensions["scan_digest"] != "scan-1"
    assert all(len(identifier.split(":", 1)[1]) == 64 for identifier in evidence.proof.evidence_ids)


def test_unscoped_populations_block_even_when_window_counts_match_payload() -> None:
    evidence = extract(_database(scan_scoped_populations=False), _request())
    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.blocked_boundaries == (
        "durable_oracle.canonical_activities",
        "durable_oracle.context_events",
    )


def test_scan_scoped_populations_exclude_another_scan_in_the_same_window() -> None:
    connection = _database()
    connection.execute(
        "INSERT INTO scan_runs VALUES ('scan-2', 'succeeded', ?, 4)", (END.isoformat(),)
    )
    connection.executemany(
        "INSERT INTO source_session_records VALUES ('scan-2', ?)", [("log",), ("trace",)]
    )
    connection.execute("INSERT INTO canonical_activities VALUES (?, 'scan-2')", (END.isoformat(),))
    connection.execute(
        "INSERT INTO session_context_events VALUES (?, 'scan-2')", (END.isoformat(),)
    )
    connection.execute("INSERT INTO outbox_drain_attempts VALUES ('drain-2', 1, 1)")
    payload = json.loads(
        connection.execute(
            "SELECT payload_json FROM otlp_outbox WHERE event_id = 'snapshot-1'"
        ).fetchone()[0]
    )
    payload["entity.id"] = "scan-2"
    payload["pipeline.bounded_drain_id"] = "drain-2"
    connection.execute(
        "INSERT INTO otlp_outbox VALUES ('snapshot-2', ?, 'delivered')", (json.dumps(payload),)
    )

    evidence = extract(connection, _request())

    assert evidence.proof.result is ExperimentResult.PROVEN
    assert evidence.proof.metrics["latest_context_events"] == 1
    assert evidence.proof.metrics["latest_canonical_activities"] == 1
    assert len(evidence.primitives) == 2
    assert [primitive.ordinal for primitive in evidence.primitives] == [0, 1]


def test_missing_snapshot_authority_fields_block_without_partial_primitives() -> None:
    for field in (
        "event_id",
        "scan.completed_at_ns",
        "pipeline.payload_schema_version",
        "rows",
        "scan.duration_ms",
        "scan.error_class",
        "logs.count",
        "traces.count",
        "context_events.count",
        "canonical_activities.count",
        "source_sessions.count",
        "outbox.pending.count",
        "outbox.failed_during_drain.count",
        "pipeline.bounded_drain_id",
    ):
        evidence = extract(_database(omit_field=field), _request())
        assert evidence.proof.result is ExperimentResult.BLOCKED
        assert evidence.primitives == ()
        assert evidence.remote_query_id is None


def test_missing_schema_field_blocks_and_conflicting_snapshots_fail() -> None:
    blocked = extract(_database(omit_schema=True), _request())
    assert blocked.proof.result is ExperimentResult.BLOCKED
    assert blocked.proof.blocked_boundaries == ("snapshot.payload_schema_version",)

    conflicted = extract(_database(duplicate_snapshot=True), _request())
    assert conflicted.proof.result is ExperimentResult.FAILED
    assert "conflicting snapshot candidates" in conflicted.proof.proposal


def test_durable_status_must_match_payload_terminal_and_error_classes() -> None:
    payload_by_status = {
        "succeeded": ("failed", "scan-error"),
        "no_data": ("failed", "scan-error"),
        "failed": ("completed", "none"),
    }
    for status, (terminal, error) in payload_by_status.items():
        evidence = extract(
            _database(
                scan_status=status,
                payload_terminal=terminal,
                payload_error=error,
            ),
            _request(),
        )
        assert evidence.proof.result is ExperimentResult.FAILED
        assert evidence.proof.assertions["snapshot_contract_valid"] is False


def test_absent_drain_attempt_authority_blocks() -> None:
    evidence = extract(_database(include_attempt_authority=False), _request())
    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.blocked_boundaries == ("durable_population_oracle",)


def test_current_pending_equality_cannot_substitute_for_immutable_drain_population() -> None:
    evidence = extract(_database(include_attempt_authority=False, current_pending=1), _request())
    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.blocked_boundaries == ("durable_population_oracle",)


def test_immutable_bounded_authority_can_prove_exact_zero() -> None:
    evidence = extract(
        _database(immutable_pending=0, immutable_failed=0, current_pending=0), _request()
    )
    assert evidence.proof.result is ExperimentResult.PROVEN
    assert evidence.proof.metrics["latest_pending_outbox"] == 0

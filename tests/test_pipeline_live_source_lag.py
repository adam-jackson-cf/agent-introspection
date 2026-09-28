from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from experiments.dashboard_prototype.contracts import ExperimentResult
from experiments.dashboard_prototype.pipeline_live_common import LiveProofRequest
from experiments.dashboard_prototype.pipeline_live_source_lag import extract


def _request() -> LiveProofRequest:
    return LiveProofRequest(
        "live-source-lag", datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 8, 2, tzinfo=UTC)
    )


def _timestamp_ns(timestamp: datetime) -> int:
    delta = timestamp.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


def _connection(*, lifecycle_authority: bool = True) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    lifecycle_table = (
        "CREATE TABLE session_context_events (event_id TEXT, producer TEXT, occurred_at TEXT);"
        if lifecycle_authority
        else ""
    )
    connection.executescript(
        """
        CREATE TABLE scan_runs (
            id TEXT PRIMARY KEY, status TEXT, started_at TEXT, completed_at TEXT,
            source_start_ns INTEGER, source_end_ns INTEGER
        );
        CREATE TABLE source_session_records (
            scan_run_id TEXT, source_kind TEXT, service_name TEXT, source_id TEXT,
            source_timestamp TEXT, source_timestamp_ns TEXT, context_evidence_id TEXT,
            session_ids_json TEXT, thread_ids_json TEXT, legacy_thread_ids_json TEXT,
            gen_ai_conversation_ids_json TEXT
        );
        """
        + lifecycle_table
    )
    return connection


def _insert_scan(
    connection: sqlite3.Connection, scan_id: str, completed: datetime, bound: datetime
) -> None:
    connection.execute(
        "INSERT INTO scan_runs VALUES (?, 'succeeded', ?, ?, ?, ?)",
        (
            scan_id,
            (completed - timedelta(seconds=5)).isoformat(),
            completed.isoformat(),
            _timestamp_ns(_request().start),
            _timestamp_ns(bound),
        ),
    )


@dataclass(frozen=True, slots=True)
class _Record:
    scan_id: str
    source: tuple[str, str]
    timestamp: datetime
    kind: str
    timestamp_ns: int | None = None
    context_evidence_id: str | None = None


def _insert_record(connection: sqlite3.Connection, record: _Record) -> None:
    service, source_id = record.source
    connection.execute(
        "INSERT INTO source_session_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            record.scan_id,
            record.kind,
            service,
            source_id,
            record.timestamp.isoformat(),
            str(record.timestamp_ns) if record.timestamp_ns is not None else None,
            record.context_evidence_id,
            '["native-id"]',
            '["thread-id"]',
            "[]",
            "[]",
        ),
    )


def _insert_lifecycle(
    connection: sqlite3.Connection, producer: str, event_id: str, timestamp: datetime
) -> None:
    connection.execute(
        "INSERT INTO session_context_events VALUES (?, ?, ?)",
        (event_id, producer, timestamp.isoformat()),
    )


def _insert_all_authorities(
    connection: sqlite3.Connection, scan_id: str, timestamp: datetime
) -> None:
    for service in ("codex-cli", "codex-app-server", "omp"):
        event_id = f"{scan_id}-{service}-event"
        for kind in ("log", "trace"):
            _insert_record(
                connection,
                _Record(
                    scan_id,
                    (service, f"{scan_id}-{service}-{kind}"),
                    timestamp,
                    kind,
                    _timestamp_ns(timestamp),
                    event_id if kind == "log" else None,
                ),
            )
        _insert_lifecycle(connection, service, event_id, timestamp)


def test_extracts_fixed_nine_cohorts_and_timestamps_primitives_at_scan_completion() -> None:
    connection = _connection()
    completed = datetime(2026, 8, 1, 12, tzinfo=UTC)
    _insert_scan(connection, "scan", completed, completed)
    stale = _request().start - timedelta(days=1)
    _insert_all_authorities(connection, "scan", stale)
    _insert_record(
        connection,
        _Record(
            "scan",
            ("omp", "nanosecond-eligible"),
            stale,
            "log",
            _timestamp_ns(completed) - 1,
        ),
    )
    _insert_record(
        connection,
        _Record(
            "scan",
            ("omp", "nanosecond-future"),
            stale,
            "log",
            _timestamp_ns(completed) + 1,
        ),
    )
    _insert_record(connection, _Record("scan", ("claude-code", "excluded"), completed, "log"))
    evidence = extract(connection, _request())

    assert evidence.proof.result is ExperimentResult.PROVEN
    assert len(evidence.primitives) == 9
    assert evidence.proof.metrics["population"] == 9
    assert evidence.proof.metrics["accepted"] == 9
    assert all(row.source_time == completed for row in evidence.primitives)
    assert all(len(str(row.dimensions["cohort"])) == 64 for row in evidence.primitives)
    assert len({row.dimensions["cohort"] for row in evidence.primitives}) == 9
    assert sorted(row.measures["lag_seconds"] for row in evidence.primitives) == [
        1 / 1_000_000_000,
        *[(completed - stale).total_seconds()] * 8,
    ]
    assert "claude" not in evidence.proof.canonical_json()
    assert len(evidence.remote_oracle) == 9


def test_clock_skew_is_recorded_without_selecting_post_bound_sources() -> None:
    connection = _connection()
    completed = datetime(2026, 8, 1, 12, tzinfo=UTC)
    _insert_scan(connection, "scan", completed, completed)
    _insert_all_authorities(connection, "scan", completed + timedelta(seconds=1))
    _insert_lifecycle(connection, "omp", "unreferenced-future", completed + timedelta(days=1))
    _insert_lifecycle(connection, "omp", "unreferenced-history", completed - timedelta(days=1))

    evidence = extract(connection, _request())

    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert len(evidence.primitives) == 9
    assert evidence.proof.metrics["accepted"] == 0
    assert evidence.proof.metrics["negative_skew_count"] == 9
    assert all(row.measures["negative_skew"] == 1 for row in evidence.primitives)
    assert all("lag_seconds" not in row.measures for row in evidence.primitives)
    assert all(
        metrics["n"] == 0
        and metrics["negative_skew_count"] == 1
        and "p50_lag_seconds" not in metrics
        and "p95_lag_seconds" not in metrics
        for metrics in evidence.remote_oracle.values()
    )


def test_missing_trace_or_lifecycle_observation_blocks_and_conserves_nine_cohorts() -> None:
    connection = _connection()
    completed = datetime(2026, 8, 1, 12, tzinfo=UTC)
    _insert_scan(connection, "scan", completed, completed)
    _insert_all_authorities(connection, "scan", completed - timedelta(seconds=1))
    connection.execute(
        "DELETE FROM source_session_records WHERE service_name = 'omp' AND source_kind = 'trace'"
    )
    connection.execute("DELETE FROM session_context_events WHERE producer = 'codex-cli'")

    evidence = extract(connection, _request())
    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert len(evidence.primitives) == 9
    assert evidence.proof.metrics["population"] == 9
    assert evidence.proof.metrics["accepted"] == 7
    assert any(
        "source-observation:omp/omp/traces" in item for item in evidence.proof.blocked_boundaries
    )
    assert any(
        "source-observation:codex-cli/codex-cli/lifecycle" in item
        for item in evidence.proof.blocked_boundaries
    )


def test_datetime_only_raw_source_rows_remain_unavailable() -> None:
    connection = _connection()
    completed = datetime(2026, 8, 1, 12, tzinfo=UTC)
    _insert_scan(connection, "scan", completed, completed)
    _insert_all_authorities(connection, "scan", completed - timedelta(seconds=1))
    connection.execute(
        """
        UPDATE source_session_records
        SET source_timestamp_ns = NULL
        WHERE service_name = 'omp' AND source_kind = 'log'
        """
    )

    evidence = extract(connection, _request())

    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert any(
        boundary.startswith("source-lag.raw-authority:")
        for boundary in evidence.proof.blocked_boundaries
    )


def test_absent_lifecycle_authority_remains_blocked() -> None:
    connection = _connection(lifecycle_authority=False)
    completed = datetime(2026, 8, 1, 12, tzinfo=UTC)
    _insert_scan(connection, "scan", completed, completed)
    for service in ("codex-cli", "codex-app-server", "omp"):
        for kind in ("log", "trace"):
            _insert_record(
                connection, _Record("scan", (service, f"{service}-{kind}"), completed, kind)
            )

    evidence = extract(connection, _request())

    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert len(evidence.primitives) == 9
    assert "lifecycle-authority" in evidence.proof.blocked_boundaries


def test_membership_is_start_open_end_closed_and_order_is_stable() -> None:
    connection = _connection()
    request = _request()
    _insert_scan(connection, "start", request.start, request.start)
    _insert_scan(connection, "end", request.end, request.end)
    _insert_all_authorities(connection, "start", request.start)
    _insert_all_authorities(connection, "end", request.end)

    first = extract(connection, request)
    second = extract(connection, request)

    assert first.proof.result is ExperimentResult.PROVEN
    assert len(first.primitives) == 9
    assert first.primitives == second.primitives


def test_scan_scoped_observations_do_not_conflict_across_scans() -> None:
    connection = _connection()
    completed = datetime(2026, 8, 1, 12, tzinfo=UTC)
    _insert_scan(connection, "first", completed, completed)
    _insert_scan(
        connection, "second", completed + timedelta(minutes=1), completed + timedelta(minutes=1)
    )
    _insert_record(
        connection,
        _Record("first", ("omp", "same"), completed - timedelta(seconds=1), "log"),
    )
    _insert_record(
        connection,
        _Record("second", ("omp", "same"), completed + timedelta(seconds=1), "log"),
    )

    assert extract(connection, _request()).proof.result is ExperimentResult.BLOCKED

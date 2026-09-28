import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_introspection.config import SchedulerConfig
from experiments.dashboard_prototype.pipeline_capture import capture
from experiments.dashboard_prototype.pipeline_live_common import LiveProofRequest


def test_capture_persists_only_bounded_allowlisted_pipeline_inputs(tmp_path: Path) -> None:
    source = sqlite3.connect(":memory:")
    source.executescript(
        """
        CREATE TABLE scan_runs (
            id TEXT, status TEXT, started_at TEXT, completed_at TEXT,
            source_start_ns INTEGER, source_end_ns INTEGER, rows_processed INTEGER,
            error_code TEXT, details_json TEXT
        );
        CREATE TABLE source_session_records (
            scan_run_id TEXT, source_kind TEXT, service_name TEXT, source_id TEXT,
            source_timestamp TEXT, source_timestamp_ns TEXT, context_evidence_id TEXT,
            forbidden_payload TEXT
        );
        CREATE TABLE session_context_events (
            event_id TEXT, producer TEXT, occurred_at TEXT, forbidden_payload TEXT
        );
        CREATE TABLE scan_execution_inputs (
            scan_run_id TEXT, monotonic_started REAL, monotonic_finished REAL,
            logs_json TEXT, traces_json TEXT, context_events_json TEXT,
            canonical_activities_json TEXT
        );
        CREATE TABLE otlp_outbox (event_id TEXT, payload_json TEXT);
        """
    )
    start = datetime(2026, 9, 5, 12, tzinfo=UTC)
    completion = start + timedelta(minutes=1)
    source.execute(
        "INSERT INTO scan_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "scan-1",
            "succeeded",
            start.isoformat(),
            completion.isoformat(),
            1,
            2,
            3,
            None,
            """{"logs":1,"traces":2,"session_context_events":3,"canonical_activities":4,"source_sessions":5}""",
        ),
    )
    source.execute(
        "INSERT INTO source_session_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "scan-1",
            "log",
            "omp",
            "source-1",
            completion.isoformat(),
            "1788264060000000000",
            "context-1",
            "secret",
        ),
    )
    source.execute(
        "INSERT INTO session_context_events VALUES (?, ?, ?, ?)",
        ("context-1", "omp", completion.isoformat(), "secret"),
    )
    source.execute(
        "INSERT INTO scan_execution_inputs VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("scan-1", 10.5, 11.25, '["source-1","source-1"]', "[]", "[]", "[]"),
    )
    source.execute(
        "INSERT INTO otlp_outbox VALUES (?, ?)",
        (
            "native-snapshot-1",
            """{"entity.id":"scan-1","event.name":"introspection.pipeline.snapshot","timestamp_ns":1788264600000000000,"pipeline.state":"healthy","scan.terminal_status":"succeeded","pipeline.freshness":"fresh","logs.query_status":"available","logs.data_state":"available","traces.query_status":"available","traces.data_state":"available","hydration.query_status":"available","hydration.data_state":"available","scan.duration_ms":250.5,"rows.processed":3,"outbox.pending_after_drain_excluding_terminal_event":2}""",
        ),
    )
    source.execute(
        "INSERT INTO otlp_outbox VALUES (?, ?)",
        (
            "legacy-snapshot-1",
            """{"entity.id":"scan-1","event.name":"dashboard.pipeline.snapshot","timestamp_ns":1788264600000000000}""",
        ),
    )
    source.commit()

    destination = capture(
        source,
        tmp_path / "capture.sqlite3",
        LiveProofRequest("run-1", start, start + timedelta(hours=1)),
        SchedulerConfig(),
    )

    observed = sqlite3.connect(f"file:{destination}?mode=ro", uri=True)
    assert observed.execute("SELECT id, rows_processed FROM scan_runs").fetchall() == [
        ("scan-1", 3)
    ]
    assert observed.execute(
        "SELECT source_id, source_timestamp_ns FROM source_session_records"
    ).fetchall() == [("source-1", "1788264060000000000")]
    assert observed.execute("SELECT event_id FROM session_context_events").fetchall() == [
        ("context-1",)
    ]
    assert observed.execute("SELECT interval_seconds FROM schedule_policy").fetchall() == [(300,)]
    assert observed.execute(
        "SELECT scan_run_id, monotonic_started, monotonic_finished, logs_json, traces_json, "
        "context_events_json, canonical_activities_json FROM scan_execution_inputs"
    ).fetchall() == [("scan-1", 10.5, 11.25, '["source-1","source-1"]', "[]", "[]", "[]")]
    assert observed.execute(
        "SELECT event_id, scan_run_id FROM native_pipeline_snapshots"
    ).fetchall() == [("native-snapshot-1", "scan-1")]
    assert "forbidden_payload" not in {
        row[1] for row in observed.execute("PRAGMA table_info(source_session_records)")
    }

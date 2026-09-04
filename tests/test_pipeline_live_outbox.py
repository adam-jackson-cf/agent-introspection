import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta

from experiments.dashboard_prototype import pipeline_live_outbox
from experiments.dashboard_prototype.contracts import ExperimentResult
from experiments.dashboard_prototype.pipeline_live_common import LiveProofRequest

START = datetime(2026, 9, 1, 12, tzinfo=UTC)
END = START + timedelta(minutes=10)


def _request() -> LiveProofRequest:
    return LiveProofRequest("live-outbox-1", START, END)


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE otlp_outbox ("
        "event_id TEXT, payload_json TEXT NOT NULL, status TEXT, attempt_count INTEGER, "
        "next_attempt_at TEXT, created_at TEXT, delivered_at TEXT, "
        "destination TEXT NOT NULL, event_type TEXT NOT NULL)"
    )
    return connection


def test_extracts_current_events_in_exact_window_without_payload_access(monkeypatch) -> None:
    connection = _connection()
    captured = {}
    connection.executemany(
        "INSERT INTO otlp_outbox VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            (
                "outside",
                "do-not-read",
                "pending",
                0,
                "",
                START.isoformat(),
                None,
                "otlp",
                "outbox-event",
            ),
            (
                "later",
                "do-not-read",
                "delivered",
                1,
                "",
                END.isoformat(),
                END.isoformat(),
                "otlp",
                "outbox-event",
            ),
            (
                "first",
                "do-not-read",
                "pending",
                0,
                "",
                (START + timedelta(minutes=1)).isoformat(),
                None,
                "otlp",
                "outbox-event",
            ),
            (
                "second",
                "do-not-read",
                "pending",
                0,
                "",
                (START + timedelta(minutes=2)).isoformat(),
                None,
                "otlp",
                "outbox-event",
            ),
        ),
    )
    original = pipeline_live_outbox.build_outbox_proof

    def record_boundary(**kwargs):
        captured["boundary"] = kwargs["outbox_boundary"]
        return original(**kwargs)

    monkeypatch.setattr(pipeline_live_outbox, "build_outbox_proof", record_boundary)
    statements: list[str] = []
    connection.set_trace_callback(statements.append)

    evidence = pipeline_live_outbox.extract(connection, _request())

    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.blocked_boundaries == (
        "attempts",
        "final drain id",
        "scan completed at",
    )
    events = tuple(captured["boundary"].events)
    assert [event.event_id for event in events] == [
        hashlib.sha256(b"first").hexdigest(),
        hashlib.sha256(b"second").hexdigest(),
        hashlib.sha256(b"later").hexdigest(),
    ]
    assert [event.is_pending for event in events] == [True, True, False]
    primitive_events = [
        row
        for row in evidence.primitives
        if isinstance(row, pipeline_live_outbox.E5OutboxEventPrimitive)
    ]
    assert len(primitive_events) == len(evidence.primitives)
    assert [row.status for row in primitive_events] == [
        pipeline_live_outbox.OutboxEventStatus.PENDING,
        pipeline_live_outbox.OutboxEventStatus.PENDING,
        pipeline_live_outbox.OutboxEventStatus.DELIVERED,
    ]


def test_conflicting_current_rows_fail_closed() -> None:
    connection = _connection()
    created_at = (START + timedelta(minutes=1)).isoformat()
    connection.executemany(
        "INSERT INTO otlp_outbox VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            (
                "same",
                "do-not-read",
                "pending",
                0,
                "",
                created_at,
                None,
                "otlp",
                "outbox-event",
            ),
            (
                "same",
                "do-not-read",
                "delivered",
                0,
                "",
                created_at,
                None,
                "otlp",
                "outbox-event",
            ),
        ),
    )

    evidence = pipeline_live_outbox.extract(connection, _request())

    assert evidence.proof.result is ExperimentResult.FAILED
    assert evidence.proof.assertions == {"outbox_rows_consistent": False}
    assert evidence.primitives == ()
    assert evidence.remote_query_id is None


def _add_delivery_authority(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE otlp_outbox_drains (drain_id TEXT, completed_at TEXT, is_final INTEGER)"
    )
    connection.execute(
        "CREATE TABLE otlp_outbox_delivery_attempts "
        "(attempt_id TEXT, event_id TEXT, drain_id TEXT, attempted_at TEXT, "
        "status TEXT, error_class TEXT)"
    )


def test_emits_complete_typed_final_drain_authority_without_raw_errors() -> None:
    connection = _connection()
    _add_delivery_authority(connection)
    first_created = START + timedelta(minutes=1)
    second_created = START + timedelta(minutes=2)
    connection.executemany(
        "INSERT INTO otlp_outbox VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            (
                "first",
                "do-not-read",
                "pending",
                0,
                "",
                first_created.isoformat(),
                None,
                "otlp",
                "outbox-event",
            ),
            (
                "second",
                "do-not-read",
                "delivered",
                1,
                "",
                second_created.isoformat(),
                END.isoformat(),
                "otlp",
                "outbox-event",
            ),
        ),
    )
    connection.execute(
        "INSERT INTO otlp_outbox_drains VALUES (?, ?, ?)",
        ("final-drain", END.isoformat(), 1),
    )
    connection.executemany(
        "INSERT INTO otlp_outbox_delivery_attempts VALUES (?, ?, ?, ?, ?, ?)",
        (
            (
                "first-attempt",
                "first",
                "final-drain",
                (START + timedelta(minutes=3)).isoformat(),
                "failed",
                "NetworkTimeout",
            ),
            (
                "second-attempt",
                "second",
                "final-drain",
                (START + timedelta(minutes=4)).isoformat(),
                "delivered",
                None,
            ),
            (
                "other-attempt",
                "first",
                "other-drain",
                (START + timedelta(minutes=5)).isoformat(),
                "failed",
                "MustNotAppear",
            ),
        ),
    )

    evidence = pipeline_live_outbox.extract(connection, _request())

    assert evidence.proof.result is ExperimentResult.PROVEN
    assert evidence.remote_query_id == "pipeline-outbox-current-pending-v1"
    event_rows = [
        row
        for row in evidence.primitives
        if isinstance(row, pipeline_live_outbox.E5OutboxEventPrimitive)
    ]
    attempt_rows = [
        row
        for row in evidence.primitives
        if isinstance(row, pipeline_live_outbox.E5DeliveryAttemptPrimitive)
    ]
    drain_rows = [
        row
        for row in evidence.primitives
        if isinstance(row, pipeline_live_outbox.E5FinalDrainPrimitive)
    ]
    assert [
        (row.event_id, row.created_at, row.destination, row.event_type, row.status)
        for row in event_rows
    ] == [
        (
            hashlib.sha256(b"first").hexdigest(),
            first_created,
            "otlp",
            "outbox-event",
            pipeline_live_outbox.OutboxEventStatus.PENDING,
        ),
        (
            hashlib.sha256(b"second").hexdigest(),
            second_created,
            "otlp",
            "outbox-event",
            pipeline_live_outbox.OutboxEventStatus.DELIVERED,
        ),
    ]
    assert [
        (
            row.attempt_id,
            row.event_id,
            row.drain_id,
            row.attempted_at,
            row.status,
            row.error_class,
        )
        for row in attempt_rows
    ] == [
        (
            hashlib.sha256(b"first-attempt").hexdigest(),
            hashlib.sha256(b"first").hexdigest(),
            hashlib.sha256(b"final-drain").hexdigest(),
            START + timedelta(minutes=3),
            pipeline_live_outbox.DeliveryAttemptStatus.FAILED,
            "NetworkTimeout",
        ),
        (
            hashlib.sha256(b"second-attempt").hexdigest(),
            hashlib.sha256(b"second").hexdigest(),
            hashlib.sha256(b"final-drain").hexdigest(),
            START + timedelta(minutes=4),
            pipeline_live_outbox.DeliveryAttemptStatus.DELIVERED,
            None,
        ),
    ]
    assert drain_rows == [
        pipeline_live_outbox.E5FinalDrainPrimitive(
            experiment_id=evidence.proof.experiment_id,
            drain_id=hashlib.sha256(b"final-drain").hexdigest(),
            completed_at=END,
        )
    ]
    assert "MustNotAppear" not in repr(evidence.primitives)


def test_raw_delivery_error_fails_closed_without_exposure() -> None:
    connection = _connection()
    _add_delivery_authority(connection)
    connection.execute(
        "INSERT INTO otlp_outbox VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "event",
            "do-not-read",
            "pending",
            0,
            "",
            (START + timedelta(minutes=1)).isoformat(),
            None,
            "otlp",
            "outbox-event",
        ),
    )
    connection.execute(
        "INSERT INTO otlp_outbox_drains VALUES (?, ?, ?)",
        ("final-drain", END.isoformat(), 1),
    )
    connection.execute(
        "INSERT INTO otlp_outbox_delivery_attempts VALUES (?, ?, ?, ?, ?, ?)",
        (
            "attempt",
            "event",
            "final-drain",
            (START + timedelta(minutes=2)).isoformat(),
            "failed",
            "connection refused https://secret.example",
        ),
    )

    evidence = pipeline_live_outbox.extract(connection, _request())

    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert "connection refused" not in repr(evidence.primitives)

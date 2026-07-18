import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_introspection.database import connect_database
from agent_introspection.telemetry import (
    OPERATIONAL_SCOPE,
    DerivedEvent,
    drain_outbox,
    enqueue_event,
    enqueue_events,
    enqueue_observation_reconciliation,
    plan_observation_reconciliation,
    remote_observation_event_ids,
)


def outbox_database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE otlp_outbox (
            event_id TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            attempt_count INTEGER NOT NULL,
            next_attempt_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            delivered_at TEXT
        )
        """
    )
    return connection


def event() -> DerivedEvent:
    return DerivedEvent(
        scope="generation:generation-1",
        entity_id="finding-1",
        entity_version=2,
        event_sequence=3,
        event_name="introspection.trend.promoted",
        attributes={"trend.state": "actionable", "occurrence.count": 3},
        timestamp_ns=1_700_000_000_000_000_000,
    )


def test_duplicate_enqueue_reuses_identical_event_id_and_payload() -> None:
    connection = outbox_database()
    first = enqueue_event(connection, event())
    second = enqueue_event(connection, event())
    rows = connection.execute("SELECT event_id, payload_json FROM otlp_outbox").fetchall()
    assert first == second
    assert len(rows) == 1
    assert rows[0][0] == first
    assert json.loads(rows[0][1])["event.id"] == first


def test_failed_delivery_retains_identical_payload_for_retry() -> None:
    connection = outbox_database()
    enqueue_event(connection, event())
    before = connection.execute("SELECT payload_json FROM otlp_outbox").fetchone()[0]
    with patch("urllib.request.urlopen", side_effect=TimeoutError):
        result = drain_outbox(connection)
    after = connection.execute(
        "SELECT payload_json, attempt_count, status FROM otlp_outbox"
    ).fetchone()
    assert result == {"selected": 1, "delivered": 0, "pending": 1}
    assert after == (before, 1, "pending")


def test_empty_outbox_drain_is_a_valid_noop() -> None:
    assert drain_outbox(outbox_database()) == {"selected": 0, "delivered": 0, "pending": 0}


def test_event_batches_commit_all_deterministic_payloads() -> None:
    connection = outbox_database()
    second = DerivedEvent(
        scope="generation:generation-1",
        entity_id="finding-2",
        entity_version=1,
        event_sequence=1,
        event_name="introspection.observation.detected",
        attributes={"detector.id": "tool_failure"},
        timestamp_ns=1_700_000_000_000_000_001,
    )
    event_ids = enqueue_events(connection, [event(), second])
    assert connection.execute("SELECT COUNT(*) FROM otlp_outbox").fetchone()[0] == 2
    assert event_ids == [event().event_id, second.event_id]


def test_observation_reconciliation_preserves_original_ordinals_and_is_idempotent(
    tmp_path: Path,
) -> None:
    connection = connect_database(tmp_path / "introspection.sqlite3")
    try:
        connection.execute(
            "INSERT INTO scan_runs (id, status, started_at) VALUES ('failed-scan', 'failed', 'now')"
        )
        for index, event_id in ((1, "event-a"), (2, "event-b")):
            connection.execute(
                """
                INSERT INTO observations (
                    id, scan_run_id, detector_id, detector_version, category, project_identity_id,
                    task_identity, turn_identity, occurred_at_ns, fingerprint, operation_kind,
                    target_kind, normalized_target, normalized_failure_class, normalization_version,
                    membership_explanation, attributes_json, created_at
                ) VALUES (?, 'failed-scan', 'tool_failure', 1, 'tool_failure', NULL, 'thread:one',
                    NULL, ?, ?, 'event', 'none', 'operation', 'failure', 1, 'membership', ?, 'now'
                )
                """,
                (
                    f"observation-{index}",
                    index,
                    f"{index}" * 64,
                    json.dumps({"event_ids": [event_id]}),
                ),
            )
        connection.commit()
        enqueue_event(
            connection,
            DerivedEvent(
                scope=OPERATIONAL_SCOPE,
                entity_id="observation-1",
                entity_version=1,
                event_sequence=1,
                event_name="introspection.observation.detected",
                attributes={"detector.id": "tool_failure"},
                timestamp_ns=1,
            ),
        )

        first_plan = plan_observation_reconciliation(
            connection,
            scan_run_ids=("failed-scan",),
        )
        first = enqueue_observation_reconciliation(
            connection,
            first_plan,
            remote_event_ids=set(),
        )
        second_plan = plan_observation_reconciliation(
            connection,
            scan_run_ids=("failed-scan",),
        )

        assert first == {
            "observations": 2,
            "existing_local_observation_events": 1,
            "existing_remote_observation_events": 0,
            "queued_observation_events": 1,
        }
        assert second_plan.events == ()
        assert second_plan.existing_local_observation_events == 2
        payloads = [
            json.loads(row[0])
            for row in connection.execute("SELECT payload_json FROM otlp_outbox").fetchall()
        ]
        recovered = next(payload for payload in payloads if payload["entity.id"] == "observation-2")
        assert recovered["event.sequence"] == 2
        assert recovered["project.id"] == "unresolved"
    finally:
        connection.close()


def test_remote_observation_event_preflight_uses_parameterized_candidate_ids() -> None:
    event = DerivedEvent(
        scope="generation:generation-1",
        entity_id="observation-1",
        entity_version=1,
        event_sequence=1,
        event_name="introspection.observation.detected",
        attributes={"detector.id": "tool_failure"},
        timestamp_ns=1_700_000_000_000_000_000,
    )

    class Remote:
        def __init__(self) -> None:
            self.query_text = ""
            self.parameters: dict[str, str | int] = {}

        def query(self, sql: str, parameters: dict[str, str | int]) -> list[dict[str, str]]:
            self.query_text = sql
            self.parameters = parameters
            return [{"event_id": event.event_id}]

    remote = Remote()
    assert remote_observation_event_ids(remote, [event]) == {event.event_id}  # type: ignore[arg-type]
    assert "attributes_string['event.name'] = {event_name:String}" in remote.query_text
    assert "attributes_string['event.id'] IN ({event_0:String})" in remote.query_text
    assert remote.parameters["event_0"] == event.event_id
    assert remote.parameters["event_name"] == "introspection.observation.detected"


def test_event_scope_is_part_of_immutable_event_identity() -> None:
    generated = event()
    operational = DerivedEvent(
        scope=OPERATIONAL_SCOPE,
        entity_id=generated.entity_id,
        entity_version=generated.entity_version,
        event_sequence=generated.event_sequence,
        event_name=generated.event_name,
        attributes=generated.attributes,
        timestamp_ns=generated.timestamp_ns,
    )
    assert generated.event_id != operational.event_id
    assert generated.payload()["event.scope"] == "generation:generation-1"


def test_observation_reconciliation_requires_explicit_failed_scan(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "introspection.sqlite3")
    try:
        connection.execute(
            "INSERT INTO scan_runs (id, status, started_at) VALUES ('scan-1', 'succeeded', 'now')"
        )
        connection.commit()
        with pytest.raises(ValueError, match="not failed"):
            plan_observation_reconciliation(connection, scan_run_ids=("scan-1",))
        assert connection.execute("SELECT COUNT(*) FROM otlp_outbox").fetchone()[0] == 0
    finally:
        connection.close()

import hashlib
import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_introspection.database import connect_database
from agent_introspection.telemetry import (
    CANONICAL_ACTIVITY_EVENT_NAME,
    CANONICAL_ACTIVITY_PAYLOAD_SCHEMA_VERSION,
    OPERATIONAL_SCOPE,
    REVIEW_SCOPE,
    CanonicalActivityVersionEvent,
    DerivedEvent,
    drain_outbox,
    drain_outbox_event_ids,
    enqueue_canonical_activity_version,
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


def canonical_activity_database(tmp_path: Path) -> sqlite3.Connection:
    connection = connect_database(tmp_path / "canonical-activity.sqlite3")
    connection.execute(
        """
        INSERT INTO canonical_activities (
            id, producer, producer_surface, correlation_id, source_started_at_ns,
            source_ended_at_ns, detector_id, detector_version, normalization_version,
            source_membership_hash, source_membership_json, operation_kind, target_kind,
            normalized_target, normalized_failure_class, created_at
        ) VALUES (
            'activity-1', 'codex-cli', 'test', 'correlation-1', 10, 20, 'tool_failure',
            1, 1, ?, '["source-1"]', 'run', 'command', 'test', 'failure', 'now'
        )
        """,
        ("a" * 64,),
    )
    connection.execute(
        """
        INSERT INTO canonical_activity_versions (
            activity_id, version, attribution_state, project_identity_id,
            attribution_method, attribution_evidence_id, reason_code, created_at
        ) VALUES ('activity-1', 1, 'unresolved', NULL, 'source', NULL, 'missing_workspace', 'now')
        """
    )
    connection.commit()
    return connection


def event() -> DerivedEvent:
    return DerivedEvent(
        scope=OPERATIONAL_SCOPE,
        entity_id="finding-1",
        entity_version=2,
        event_sequence=3,
        event_name="introspection.trend.promoted",
        attributes={"trend.state": "actionable", "occurrence.count": 3},
        timestamp_ns=1_700_000_000_000_000_000,
    )


def canonical_activity_event(
    *, attributes: dict[str, str | int] | None = None
) -> CanonicalActivityVersionEvent:
    return CanonicalActivityVersionEvent(
        activity_id="activity-1",
        version=1,
        timestamp_ns=20,
        attributes={"attribution.state": "unresolved"} if attributes is None else attributes,
    )


def test_canonical_activity_event_id_hashes_only_contract_fields() -> None:
    event = canonical_activity_event()
    expected = hashlib.sha256(
        "\x1f".join(
            (
                "activity-1",
                "1",
                str(CANONICAL_ACTIVITY_PAYLOAD_SCHEMA_VERSION),
                CANONICAL_ACTIVITY_EVENT_NAME,
            )
        ).encode()
    ).hexdigest()
    assert event.event_id == expected
    assert len(event.event_id) == 64
    assert (
        event.event_id
        == canonical_activity_event(attributes={"attribution.state": "resolved"}).event_id
    )


def test_canonical_activity_enqueue_reuses_identical_identity_and_evidence(tmp_path: Path) -> None:
    connection = canonical_activity_database(tmp_path)
    try:
        with pytest.raises(ValueError, match="requires a caller-owned transaction"):
            enqueue_canonical_activity_version(connection, canonical_activity_event())
        assert not connection.in_transaction

        connection.execute("BEGIN")
        first = enqueue_canonical_activity_version(connection, canonical_activity_event())
        connection.commit()

        connection.execute("BEGIN")
        second = enqueue_canonical_activity_version(connection, canonical_activity_event())
        connection.commit()
        assert first == second
        assert connection.execute("SELECT COUNT(*) FROM otlp_outbox").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT event_id FROM canonical_activity_outbox_evidence"
            ).fetchone()[0]
            == first
        )
    finally:
        connection.close()


def test_canonical_activity_enqueue_rejects_conflicting_payload(tmp_path: Path) -> None:
    connection = canonical_activity_database(tmp_path)
    try:
        connection.execute("BEGIN")
        enqueue_canonical_activity_version(connection, canonical_activity_event())
        connection.commit()

        connection.execute("BEGIN")
        with pytest.raises(ValueError, match=r"activity-1.*attribution.state"):
            enqueue_canonical_activity_version(
                connection,
                canonical_activity_event(attributes={"attribution.state": "resolved"}),
            )
        connection.rollback()
    finally:
        connection.close()


def test_canonical_activity_enqueue_rolls_back_outbox_and_evidence(tmp_path: Path) -> None:
    connection = canonical_activity_database(tmp_path)
    try:
        connection.execute("BEGIN")
        with pytest.raises(RuntimeError, match="rollback"):
            enqueue_canonical_activity_version(connection, canonical_activity_event())
            raise RuntimeError("rollback")
        connection.rollback()
        assert connection.execute("SELECT COUNT(*) FROM otlp_outbox").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM canonical_activity_outbox_evidence"
            ).fetchone()[0]
            == 0
        )
    finally:
        connection.close()


def test_duplicate_enqueue_reuses_identical_event_id_and_payload() -> None:
    connection = outbox_database()
    first = enqueue_event(connection, event())
    second = enqueue_event(connection, event())
    rows = connection.execute("SELECT event_id, payload_json FROM otlp_outbox").fetchall()
    assert first == second
    assert len(rows) == 1
    assert rows[0][0] == first
    assert json.loads(rows[0][1])["event.id"] == first


def test_conflicting_payload_reports_immutable_identity_and_changed_fields() -> None:
    connection = outbox_database()
    enqueue_event(connection, event())
    conflicting = DerivedEvent(
        scope=OPERATIONAL_SCOPE,
        entity_id="finding-1",
        entity_version=2,
        event_sequence=3,
        event_name="introspection.trend.promoted",
        attributes={"trend.state": "isolated", "occurrence.count": 3},
        timestamp_ns=1_700_000_000_000_000_000,
    )

    with pytest.raises(ValueError, match=r"entity_id=finding-1.*trend.state"):
        enqueue_event(connection, conflicting)

    assert connection.execute("SELECT COUNT(*) FROM otlp_outbox").fetchone()[0] == 1


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


def test_exact_outbox_delivery_does_not_select_unrelated_pending_events() -> None:
    connection = outbox_database()
    selected_id = enqueue_event(connection, event())
    unrelated = DerivedEvent(
        scope=OPERATIONAL_SCOPE,
        entity_id="finding-2",
        entity_version=1,
        event_sequence=1,
        event_name="introspection.observation.detected",
        attributes={"detector.id": "tool_failure"},
        timestamp_ns=1_700_000_000_000_000_001,
    )
    unrelated_id = enqueue_event(connection, unrelated)
    response = MagicMock(status=200)
    response.__enter__.return_value = response
    with patch("urllib.request.urlopen", return_value=response):
        result = drain_outbox_event_ids(connection, [selected_id])
    assert result == {"selected": 1, "delivered": 1, "pending": 0}
    assert connection.execute(
        "SELECT status FROM otlp_outbox WHERE event_id = ?", (unrelated_id,)
    ).fetchone() == ("pending",)


def test_event_batches_commit_all_deterministic_payloads() -> None:
    connection = outbox_database()
    second = DerivedEvent(
        scope=OPERATIONAL_SCOPE,
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
        connection.execute(
            """
            INSERT INTO project_identities (
                id, identity_kind, canonical_path, canonical_name, git_common_dir, created_at
            ) VALUES ('historical-project', 'git', '/historical/project', NULL, NULL, 'now')
            """
        )
        for index, event_id in ((1, "event-a"), (2, "event-b")):
            connection.execute(
                """
                INSERT INTO observations (
                    id, scan_run_id, detector_id, detector_version, category, project_identity_id,
                    task_identity, turn_identity, occurred_at_ns, fingerprint, operation_kind,
                    target_kind, normalized_target, normalized_failure_class, normalization_version,
                    membership_explanation, attributes_json, created_at
                ) VALUES (
                    ?, 'failed-scan', 'tool_failure', 1, 'tool_failure', 'historical-project',
                    'thread:one', NULL, ?, ?, 'event', 'none', 'operation', 'failure', 1,
                    'membership', ?, 'now'
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
        assert recovered["agent.project.id"] == "unresolved"
        assert recovered["agent.project.name"] == "unresolved"
        assert "project.id" not in recovered
        assert "project.name" not in recovered
    finally:
        connection.close()


def test_remote_observation_event_preflight_uses_parameterized_candidate_ids() -> None:
    event = DerivedEvent(
        scope=OPERATIONAL_SCOPE,
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
    review = DerivedEvent(
        scope=REVIEW_SCOPE,
        entity_id=generated.entity_id,
        entity_version=generated.entity_version,
        event_sequence=generated.event_sequence,
        event_name=generated.event_name,
        attributes=generated.attributes,
        timestamp_ns=generated.timestamp_ns,
    )
    assert generated.event_id != review.event_id
    assert generated.payload()["event.scope"] == OPERATIONAL_SCOPE


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

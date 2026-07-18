import json
import sqlite3
from datetime import UTC, datetime

import pytest

from agent_introspection.review import (
    REVIEW_ACTIVITY_SNAPSHOT_EVENT,
    REVIEW_SESSION_CHANGED_EVENT,
    ReviewEnvelope,
    create_review_session,
    import_model_output,
    record_review_activity_snapshot,
)


def review_database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE review_sessions (
          id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, nonce TEXT, schema_version INTEGER,
          purpose TEXT, requested_model TEXT, requested_effort TEXT,
          ordered_candidate_ids_json TEXT, payload_hash TEXT, byte_count INTEGER,
          reserved_model_budget INTEGER, status TEXT, entity_version INTEGER,
          created_at TEXT, imported_at TEXT
        );
        CREATE TABLE model_budget_ledger (
          id TEXT PRIMARY KEY, review_session_id TEXT, entry_type TEXT, amount INTEGER,
          created_at TEXT
        );
        CREATE TABLE model_runs (
          id TEXT PRIMARY KEY, review_session_id TEXT, model TEXT, effort TEXT, trace_id TEXT,
          input_tokens INTEGER, output_tokens INTEGER, reasoning_tokens INTEGER, status TEXT,
          total_tokens INTEGER, token_availability TEXT, created_at TEXT
        );
        CREATE TABLE review_session_events (
          id TEXT PRIMARY KEY, review_session_id TEXT, entity_version INTEGER, status TEXT,
          review_run_id TEXT, created_at TEXT
        );
        CREATE TABLE review_activity_snapshots (
          id TEXT PRIMARY KEY, entity_version INTEGER, trigger_kind TEXT, trigger_id TEXT,
          trigger_version INTEGER, classification_session_count INTEGER,
          proposal_session_count INTEGER, classification_result_count INTEGER,
          proposal_result_count INTEGER, created_at TEXT
        );
        CREATE TABLE semantic_classifications (
          id TEXT PRIMARY KEY, review_session_id TEXT, candidate_id TEXT, payload_json TEXT,
          created_at TEXT
        );
        CREATE TABLE proposal_drafts (
          id TEXT PRIMARY KEY, review_session_id TEXT, candidate_id TEXT, payload_json TEXT,
          created_at TEXT
        );
        CREATE TABLE otlp_outbox (
          event_id TEXT PRIMARY KEY, payload_json TEXT, status TEXT, attempt_count INTEGER,
          next_attempt_at TEXT, created_at TEXT, delivered_at TEXT
        );
        """
    )
    return connection


def output_for(envelope: ReviewEnvelope) -> dict[str, object]:
    return {
        "session_id": envelope.session_id,
        "nonce": envelope.nonce,
        "schema_version": envelope.schema_version,
        "payload_hash": envelope.payload_hash,
        "requested_model": envelope.requested_model,
        "requested_effort": envelope.requested_effort,
        "results": [
            {"candidate_id": candidate_id, "classification": "workflow"}
            for candidate_id in envelope.ordered_candidate_ids
        ],
    }


def provenance(envelope: ReviewEnvelope, *, token_count: int = 100) -> dict[str, object]:
    return {
        "model": envelope.requested_model,
        "effort": envelope.requested_effort,
        "trace_id": "trace-1",
        "token_count": token_count,
        "input_tokens": 80,
        "output_tokens": 15,
        "reasoning_tokens": 5,
    }


def test_review_sessions_enforce_per_call_batch_and_candidate_limits() -> None:
    connection = review_database()
    first = create_review_session(
        connection,
        kind="classification",
        candidates=[{"id": f"c{i}"} for i in range(10)],
        reserved_model_budget=1_000,
    )
    for call in range(1, 4):
        create_review_session(
            connection,
            kind="classification",
            candidates=[{"id": f"c{call * 10 + i}"} for i in range(10)],
            reserved_model_budget=1_000,
            batch_id=first.batch_id,
        )
    with pytest.raises(RuntimeError, match="call limit"):
        create_review_session(
            connection,
            kind="classification",
            candidates=[{"id": "overflow"}],
            reserved_model_budget=1_000,
            batch_id=first.batch_id,
        )
    with pytest.raises(ValueError, match="per call"):
        create_review_session(
            connection,
            kind="classification",
            candidates=[{"id": f"x{i}"} for i in range(11)],
            reserved_model_budget=1_000,
        )


def test_arbitrary_unprovenanced_or_over_budget_model_json_is_rejected() -> None:
    connection = review_database()
    envelope = create_review_session(
        connection,
        kind="classification",
        candidates=[{"id": "c1"}],
        reserved_model_budget=100,
    )
    document = output_for(envelope)
    with pytest.raises(ValueError, match="provenance"):
        import_model_output(connection, document, provenance={})
    with pytest.raises(ValueError, match="budget"):
        import_model_output(connection, document, provenance=provenance(envelope, token_count=101))
    assert connection.execute(
        "SELECT status FROM review_sessions WHERE id = ?", (envelope.session_id,)
    ).fetchone() == ("exported",)
    assert connection.execute("SELECT COUNT(*) FROM model_runs").fetchone() == (0,)
    assert connection.execute("SELECT COUNT(*) FROM semantic_classifications").fetchone() == (0,)
    assert connection.execute(
        "SELECT entry_type, amount FROM model_budget_ledger "
        "WHERE review_session_id = ? ORDER BY created_at",
        (envelope.session_id,),
    ).fetchall() == [("reserved", 100)]
    document["nonce"] = "wrong"
    with pytest.raises(ValueError, match="nonce"):
        import_model_output(connection, document, provenance=provenance(envelope))


def test_valid_output_is_imported_once_with_budget_ledger() -> None:
    connection = review_database()
    envelope = create_review_session(
        connection,
        kind="classification",
        candidates=[{"id": "c1"}, {"id": "c2"}],
        reserved_model_budget=100,
    )
    document = output_for(envelope)
    import_model_output(connection, document, provenance=provenance(envelope))
    assert connection.execute("SELECT COUNT(*) FROM semantic_classifications").fetchone()[0] == 2
    assert (
        connection.execute(
            "SELECT status FROM review_sessions WHERE id = ?", (envelope.session_id,)
        ).fetchone()[0]
        == "imported"
    )
    with pytest.raises(ValueError, match="already"):
        import_model_output(connection, document, provenance=provenance(envelope))


def _event_payloads(connection: sqlite3.Connection, event_name: str) -> list[dict[str, object]]:
    return [
        json.loads(str(row[0]))
        for row in connection.execute(
            "SELECT payload_json FROM otlp_outbox ORDER BY created_at, event_id"
        ).fetchall()
        if json.loads(str(row[0]))["event.name"] == event_name
    ]


def test_review_lifecycle_events_are_atomic_allowlisted_and_versioned() -> None:
    connection = review_database()
    envelope = create_review_session(
        connection,
        kind="classification",
        candidates=[{"id": "c1"}, {"id": "c2"}],
        reserved_model_budget=100,
    )

    exported = _event_payloads(connection, REVIEW_SESSION_CHANGED_EVENT)
    assert len(exported) == 1
    assert exported[0]["event.scope"] == "review"
    assert exported[0]["entity.id"] == envelope.session_id
    assert exported[0]["entity.version"] == 1
    assert exported[0]["event.sequence"] == 1
    assert exported[0]["review.purpose"] == "classification"
    assert exported[0]["review.status"] == "exported"
    assert exported[0]["review.candidate.count"] == 2
    assert exported[0]["review.token.availability"] == "not_applicable"
    assert (
        not {
            "nonce",
            "payload_hash",
            "requested_model",
            "requested_effort",
            "trace.id",
        }
        & exported[0].keys()
    )

    import_model_output(connection, output_for(envelope), provenance=provenance(envelope))

    imported = _event_payloads(connection, REVIEW_SESSION_CHANGED_EVENT)[1]
    assert imported["entity.version"] == 2
    assert imported["event.sequence"] == 2
    assert imported["review.status"] == "imported"
    assert imported["review.result.count"] == 2
    assert imported["review.token.availability"] == "complete"
    assert imported["review.token.input"] == 80
    assert imported["review.token.output"] == 15
    assert imported["review.token.reasoning"] == 5
    assert imported["review.token.total"] == 100
    assert connection.execute(
        "SELECT entity_version, status FROM review_sessions WHERE id = ?", (envelope.session_id,)
    ).fetchone() == (2, "imported")
    assert connection.execute(
        "SELECT entity_version, status FROM review_session_events "
        "WHERE review_session_id = ? ORDER BY entity_version",
        (envelope.session_id,),
    ).fetchall() == [(1, "exported"), (2, "imported")]

    snapshots = _event_payloads(connection, REVIEW_ACTIVITY_SNAPSHOT_EVENT)
    assert len(snapshots) == 2
    assert snapshots[-1]["review.activity.availability"] == "available"
    assert snapshots[-1]["review.classification.session_count"] == 1
    assert snapshots[-1]["review.proposal.session_count"] == 0
    assert snapshots[-1]["review.classification.result_count"] == 2
    assert snapshots[-1]["review.proposal.result_count"] == 0
    assert snapshots[-1]["snapshot.trigger.kind"] == "review_session"


@pytest.mark.parametrize(
    ("components", "availability", "expected_fields"),
    [
        ({}, "unavailable", {}),
        ({"input_tokens": 7}, "partial", {"review.token.input": 7}),
        (
            {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0},
            "complete",
            {
                "review.token.input": 0,
                "review.token.output": 0,
                "review.token.reasoning": 0,
                "review.token.total": 0,
            },
        ),
    ],
)
def test_review_tokens_remain_nullable_without_synthetic_zeroes(
    components: dict[str, int], availability: str, expected_fields: dict[str, int]
) -> None:
    connection = review_database()
    envelope = create_review_session(
        connection,
        kind="classification",
        candidates=[{"id": "c1"}],
        reserved_model_budget=100,
    )
    run_provenance = provenance(envelope)
    for field in ("input_tokens", "output_tokens", "reasoning_tokens"):
        run_provenance.pop(field)
    run_provenance.update(components)

    import_model_output(connection, output_for(envelope), provenance=run_provenance)

    payload = _event_payloads(connection, REVIEW_SESSION_CHANGED_EVENT)[1]
    assert payload["review.token.availability"] == availability
    for field in (
        "review.token.input",
        "review.token.output",
        "review.token.reasoning",
        "review.token.total",
    ):
        if field in expected_fields:
            assert payload[field] == expected_fields[field]
        else:
            assert field not in payload
    run = connection.execute(
        "SELECT input_tokens, output_tokens, reasoning_tokens, total_tokens, token_availability "
        "FROM model_runs"
    ).fetchone()
    assert run == (
        components.get("input_tokens"),
        components.get("output_tokens"),
        components.get("reasoning_tokens"),
        expected_fields.get("review.token.total"),
        availability,
    )


def test_invalid_token_component_rolls_back_the_import() -> None:
    connection = review_database()
    envelope = create_review_session(
        connection,
        kind="classification",
        candidates=[{"id": "c1"}],
        reserved_model_budget=100,
    )
    run_provenance = provenance(envelope)
    run_provenance["input_tokens"] = True

    with pytest.raises(ValueError, match="input_tokens"):
        import_model_output(connection, output_for(envelope), provenance=run_provenance)

    assert connection.execute("SELECT COUNT(*) FROM model_runs").fetchone() == (0,)
    assert connection.execute(
        "SELECT status, entity_version FROM review_sessions WHERE id = ?", (envelope.session_id,)
    ).fetchone() == ("exported", 1)
    assert len(_event_payloads(connection, REVIEW_SESSION_CHANGED_EVENT)) == 1


@pytest.mark.parametrize(
    "token_fields",
    [
        {"input_tokens": 1, "output_tokens": 2, "reasoning_tokens": 3, "total_tokens": 7},
        {"input_tokens": 1, "total_tokens": 1},
        {"total_tokens": 1},
    ],
)
def test_total_tokens_requires_complete_matching_components(token_fields: dict[str, int]) -> None:
    connection = review_database()
    envelope = create_review_session(
        connection,
        kind="classification",
        candidates=[{"id": "c1"}],
        reserved_model_budget=100,
    )
    run_provenance = provenance(envelope)
    for field in ("input_tokens", "output_tokens", "reasoning_tokens"):
        run_provenance.pop(field)
    run_provenance.update(token_fields)

    with pytest.raises(ValueError, match="total_tokens"):
        import_model_output(connection, output_for(envelope), provenance=run_provenance)

    assert connection.execute("SELECT COUNT(*) FROM model_runs").fetchone() == (0,)
    assert connection.execute(
        "SELECT status, entity_version FROM review_sessions WHERE id = ?", (envelope.session_id,)
    ).fetchone() == ("exported", 1)


def test_capability_probes_are_excluded_from_review_activity() -> None:
    connection = review_database()
    connection.execute(
        """
        INSERT INTO review_sessions (
            id, batch_id, nonce, schema_version, purpose, requested_model, requested_effort,
            ordered_candidate_ids_json, payload_hash, byte_count, reserved_model_budget, status,
            entity_version, created_at, imported_at
        ) VALUES (
            'probe', 'batch', 'nonce', 1, 'capability_probe', 'requested', 'standard', '[]', ?,
            0, 1, 'imported', 2, '2026-07-17T12:00:00+00:00', '2026-07-17T12:00:00+00:00'
        )
        """,
        ("a" * 64,),
    )
    connection.execute(
        """
        INSERT INTO semantic_classifications (
            id, review_session_id, candidate_id, payload_json, created_at
        ) VALUES ('probe-result', 'probe', 'candidate', '{}', '2026-07-17T12:00:00+00:00')
        """
    )
    timestamp = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
    event = record_review_activity_snapshot(
        connection,
        trigger_kind="scan_run",
        trigger_id="scan-1",
        trigger_version=1,
        timestamp=timestamp,
    )

    assert event.attributes == {
        "review.activity.availability": "available",
        "review.classification.session_count": 0,
        "review.proposal.session_count": 0,
        "review.classification.result_count": 0,
        "review.proposal.result_count": 0,
        "snapshot.trigger.kind": "scan_run",
    }


def test_export_rolls_back_when_its_outbox_event_cannot_be_persisted() -> None:
    connection = review_database()
    connection.execute(
        """
        CREATE TRIGGER fail_review_outbox
        BEFORE INSERT ON otlp_outbox BEGIN
            SELECT RAISE(ABORT, 'outbox unavailable');
        END
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="outbox unavailable"):
        create_review_session(
            connection,
            kind="classification",
            candidates=[{"id": "c1"}],
            reserved_model_budget=100,
        )

    for table in (
        "review_sessions",
        "review_session_events",
        "review_activity_snapshots",
        "model_budget_ledger",
        "otlp_outbox",
    ):
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)

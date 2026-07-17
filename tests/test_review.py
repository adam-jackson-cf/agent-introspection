import sqlite3

import pytest

from agent_introspection.review import ReviewEnvelope, create_review_session, import_model_output


def review_database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE review_sessions (
          id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, nonce TEXT, schema_version INTEGER,
          kind TEXT, requested_model TEXT, requested_effort TEXT,
          ordered_candidate_ids_json TEXT, payload_hash TEXT, byte_count INTEGER,
          reserved_model_budget INTEGER, status TEXT, created_at TEXT, imported_at TEXT
        );
        CREATE TABLE model_budget_ledger (
          id TEXT PRIMARY KEY, review_session_id TEXT, entry_type TEXT, amount INTEGER,
          created_at TEXT
        );
        CREATE TABLE model_runs (
          id TEXT PRIMARY KEY, review_session_id TEXT, model TEXT, effort TEXT, trace_id TEXT,
          input_tokens INTEGER, output_tokens INTEGER, reasoning_tokens INTEGER, status TEXT,
          created_at TEXT
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

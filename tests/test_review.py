import sqlite3

import pytest

from agent_introspection.review import ReviewEnvelope, create_review_session, import_model_output
from tests.conftest import OpenWorkflow


def review_database(open_workflow: OpenWorkflow) -> sqlite3.Connection:
    connection: sqlite3.Connection = open_workflow(":memory:")
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
            {"candidate_id": candidate_id, "proposal": {"target": "quality command"}}
            for candidate_id in envelope.ordered_candidate_ids
        ],
    }


def provenance(envelope: ReviewEnvelope, *, token_count: object = 100) -> dict[str, object]:
    return {
        "model": envelope.requested_model,
        "effort": envelope.requested_effort,
        "trace_id": "trace-1",
        "token_count": token_count,
        "input_tokens": 80,
        "output_tokens": 15,
        "reasoning_tokens": 5,
    }


def test_review_sessions_enforce_per_call_batch_and_candidate_limits(
    open_workflow: OpenWorkflow,
) -> None:
    connection = review_database(open_workflow)
    first = create_review_session(
        connection, candidates=[{"id": "c0"}], reserved_model_budget=1_000
    )
    for call in range(1, 8):
        create_review_session(
            connection,
            candidates=[{"id": f"c{call}"}],
            reserved_model_budget=1_000,
            batch_id=first.batch_id,
        )
    with pytest.raises(RuntimeError, match="call limit"):
        create_review_session(
            connection,
            candidates=[{"id": "overflow"}],
            reserved_model_budget=1_000,
            batch_id=first.batch_id,
        )
    with pytest.raises(ValueError, match="per call"):
        create_review_session(
            connection,
            candidates=[{"id": "x1"}, {"id": "x2"}],
            reserved_model_budget=1_000,
        )


def test_arbitrary_unprovenanced_or_over_budget_model_json_is_rejected(
    open_workflow: OpenWorkflow,
) -> None:
    connection = review_database(open_workflow)
    envelope = create_review_session(
        connection,
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
    assert connection.execute("SELECT COUNT(*) FROM proposal_drafts").fetchone() == (0,)
    assert connection.execute(
        "SELECT entry_type, amount FROM model_budget_ledger "
        "WHERE review_session_id = ? ORDER BY created_at",
        (envelope.session_id,),
    ).fetchall() == [("reserved", 100)]
    document["nonce"] = "wrong"
    with pytest.raises(ValueError, match="nonce"):
        import_model_output(connection, document, provenance=provenance(envelope))


def test_valid_output_is_imported_once_with_budget_ledger(open_workflow: OpenWorkflow) -> None:
    connection = review_database(open_workflow)
    envelope = create_review_session(
        connection,
        candidates=[{"id": "c1"}],
        reserved_model_budget=100,
    )
    document = output_for(envelope)
    import_model_output(connection, document, provenance=provenance(envelope))
    assert connection.execute("SELECT COUNT(*) FROM proposal_drafts").fetchone()[0] == 1
    assert (
        connection.execute(
            "SELECT status FROM review_sessions WHERE id = ?", (envelope.session_id,)
        ).fetchone()[0]
        == "imported"
    )
    with pytest.raises(ValueError, match="already"):
        import_model_output(connection, document, provenance=provenance(envelope))


def test_complete_token_components_are_charged_when_token_count_understates_them(
    open_workflow: OpenWorkflow,
) -> None:
    connection = review_database(open_workflow)
    envelope = create_review_session(
        connection,
        candidates=[{"id": "c1"}],
        reserved_model_budget=100,
    )

    import_model_output(
        connection, output_for(envelope), provenance=provenance(envelope, token_count=1)
    )

    assert connection.execute(
        "SELECT entry_type, amount FROM model_budget_ledger WHERE review_session_id = ? "
        "ORDER BY entry_type",
        (envelope.session_id,),
    ).fetchall() == [("consumed", -100), ("reserved", 100)]


def test_complete_token_components_over_budget_are_rejected(open_workflow: OpenWorkflow) -> None:
    connection = review_database(open_workflow)
    envelope = create_review_session(
        connection,
        candidates=[{"id": "c1"}],
        reserved_model_budget=50,
    )

    with pytest.raises(ValueError, match="budget exceeded"):
        import_model_output(
            connection, output_for(envelope), provenance=provenance(envelope, token_count=1)
        )


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
    components: dict[str, int],
    availability: str,
    expected_fields: dict[str, int],
    open_workflow: OpenWorkflow,
) -> None:
    connection = review_database(open_workflow)
    envelope = create_review_session(
        connection,
        candidates=[{"id": "c1"}],
        reserved_model_budget=100,
    )
    run_provenance = provenance(envelope)
    for field in ("input_tokens", "output_tokens", "reasoning_tokens"):
        run_provenance.pop(field)
    run_provenance.update(components)

    import_model_output(connection, output_for(envelope), provenance=run_provenance)

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


def test_invalid_token_component_rolls_back_the_import(open_workflow: OpenWorkflow) -> None:
    connection = review_database(open_workflow)
    envelope = create_review_session(
        connection,
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


@pytest.mark.parametrize(
    "token_fields",
    [
        {"input_tokens": 1, "output_tokens": 2, "reasoning_tokens": 3, "total_tokens": 7},
        {"input_tokens": 1, "total_tokens": 1},
        {"total_tokens": 1},
    ],
)
def test_total_tokens_requires_complete_matching_components(
    token_fields: dict[str, int], open_workflow: OpenWorkflow
) -> None:
    connection = review_database(open_workflow)
    envelope = create_review_session(
        connection,
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


def test_export_rolls_back_when_its_budget_reservation_cannot_be_persisted(
    open_workflow: OpenWorkflow,
) -> None:
    connection = review_database(open_workflow)
    connection.execute(
        """
        CREATE TRIGGER fail_review_budget
        BEFORE INSERT ON model_budget_ledger BEGIN
            SELECT RAISE(ABORT, 'budget ledger unavailable');
        END
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="budget ledger unavailable"):
        create_review_session(
            connection,
            candidates=[{"id": "c1"}],
            reserved_model_budget=100,
        )

    for table in (
        "review_sessions",
        "model_budget_ledger",
    ):
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)


@pytest.mark.parametrize("token_count", [-1, 0, True, 1.5, "1.5", "50"])
def test_token_count_must_be_a_positive_integer_within_the_reserved_budget(
    token_count: object,
    open_workflow: OpenWorkflow,
) -> None:
    connection = review_database(open_workflow)
    envelope = create_review_session(
        connection,
        candidates=[{"id": "c1"}],
        reserved_model_budget=100,
    )

    with pytest.raises(ValueError, match="token_count"):
        import_model_output(
            connection,
            output_for(envelope),
            provenance=provenance(envelope, token_count=token_count),
        )

    assert connection.execute(
        "SELECT status, entity_version FROM review_sessions WHERE id = ?", (envelope.session_id,)
    ).fetchone() == ("exported", 1)
    assert connection.execute("SELECT COUNT(*) FROM model_runs").fetchone() == (0,)
    assert connection.execute("SELECT COUNT(*) FROM proposal_drafts").fetchone() == (0,)
    assert connection.execute(
        "SELECT entry_type, amount FROM model_budget_ledger WHERE review_session_id = ?",
        (envelope.session_id,),
    ).fetchall() == [("reserved", 100)]


def test_partial_token_components_are_charged_against_the_budget(
    open_workflow: OpenWorkflow,
) -> None:
    connection = review_database(open_workflow)
    envelope = create_review_session(
        connection, candidates=[{"id": "c1"}], reserved_model_budget=100
    )
    partial = {
        "model": envelope.requested_model,
        "effort": envelope.requested_effort,
        "trace_id": "trace-1",
        "token_count": 1,
        "input_tokens": 101,
    }
    with pytest.raises(ValueError, match="budget"):
        import_model_output(connection, output_for(envelope), provenance=partial)
    assert connection.execute("SELECT COUNT(*) FROM model_runs").fetchone() == (0,)
    assert connection.execute("SELECT COUNT(*) FROM proposal_drafts").fetchone() == (0,)


@pytest.mark.parametrize("member", [None, 7, "c1"])
def test_non_object_model_results_are_rejected_with_value_error(
    member: object, open_workflow: OpenWorkflow
) -> None:
    connection = review_database(open_workflow)
    envelope = create_review_session(
        connection, candidates=[{"id": "c1"}], reserved_model_budget=1_000
    )
    document = output_for(envelope)
    document["results"] = [member]
    with pytest.raises(ValueError, match="object"):
        import_model_output(connection, document, provenance=provenance(envelope))
    assert connection.execute(
        "SELECT status FROM review_sessions WHERE id = ?", (envelope.session_id,)
    ).fetchone() == ("exported",)
    assert connection.execute("SELECT COUNT(*) FROM proposal_drafts").fetchone() == (0,)

from agent_introspection import inbox


def test_large_rows_are_split_by_size() -> None:
    rows = [{"event_id": str(index), "blob": "x" * 5000} for index in range(100)]
    statements = inbox.insert_statements("hook_events", rows)
    assert len(statements) > 1
    assert all(len(statement) < 256 * 1024 for statement in statements)

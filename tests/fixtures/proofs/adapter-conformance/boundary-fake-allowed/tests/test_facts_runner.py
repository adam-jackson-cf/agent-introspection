from agent_introspection import facts


def test_sync_runs_the_clickhouse_client(monkeypatch):
    calls = []
    monkeypatch.setattr(facts, "runner", calls.append)
    assert calls == []

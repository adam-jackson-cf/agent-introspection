import json

from agent_introspection import candidates


def test_failure_cluster_root_is_chosen_by_the_clusters_own_failing_sessions() -> None:
    statements: list[str] = []

    def run(sql: str) -> str:
        statements.append(sql)
        return json.dumps({"project_root": "/b", "sessions": 3}) + "\n"

    subject = {"tool_family": "shell", "failure_class": "not_found"}
    pack = {"projects": [{"project": "example", "failures": 5}]}

    assert candidates.project_root(run, subject, pack) == "/b"
    (sql,) = statements
    assert "tool_calls_snapshot" in sql
    assert "c.tool_family = 'shell'" in sql
    assert "c.failure_class = 'not_found'" in sql
    assert "c.outcome = 'failed'" in sql
    assert "ORDER BY sessions DESC, project_root" in sql

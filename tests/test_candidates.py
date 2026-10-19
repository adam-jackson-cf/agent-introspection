from agent_introspection import evidence


def test_cluster_workdirs_are_the_clusters_own_failing_calls() -> None:
    sql = evidence.queries("shell", "not_found")["workdirs"]

    assert "tool_calls_snapshot" in sql
    assert "tool_family = 'shell'" in sql
    assert "failure_class = 'not_found'" in sql
    assert "outcome = 'failed'" in sql
    assert "workdir != ''" in sql
    assert "ORDER BY failures DESC, workdir" in sql
    assert "session_project" not in sql

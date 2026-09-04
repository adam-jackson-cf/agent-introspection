from datetime import UTC, datetime, timedelta

from experiments.dashboard_prototype.attribution_codex_app_server import (
    PRODUCER,
    LifecycleKind,
    LifecycleObservation,
    SourceObservation,
    reduce_codex_app_server,
)


def _at(second: int) -> datetime:
    return datetime(2026, 9, 1, tzinfo=UTC) + timedelta(seconds=second)


def _lifecycle(
    scenario: str,
    kind: LifecycleKind,
    second: int,
    *,
    session: str = "thread",
    project_kind: str = "git",
) -> LifecycleObservation:
    return LifecycleObservation(
        f"{scenario}-{kind}-{session}",
        _at(second),
        PRODUCER,
        session,
        kind,
        "project-hash",
        project_kind,
        scenario,
        True,
    )


def test_reducer_accepts_all_real_scenarios_and_reports_each_gate() -> None:
    lifecycle = tuple(
        row
        for ordinal, scenario in enumerate(("startup", "resume", "clear", "compact"))
        for row in (
            _lifecycle(scenario, LifecycleKind.START, ordinal * 10, session=f"thread-{scenario}"),
            _lifecycle(scenario, LifecycleKind.END, ordinal * 10 + 3, session=f"thread-{scenario}"),
        )
    )
    sources = tuple(
        SourceObservation(
            f"source-{scenario}",
            _at(ordinal * 10 + 1),
            PRODUCER,
            f"thread-{scenario}",
            "trace",
            "project-hash",
            "git",
        )
        for ordinal, scenario in enumerate(("startup", "resume", "clear", "compact"))
    )
    reduction = reduce_codex_app_server(lifecycle, sources)
    assert all(row.accepted for row in reduction.selections)
    assert all(dict(row.gates).values() for row in reduction.selections)
    assert not reduction.blocked_boundaries
    assert not reduction.failed_boundaries
    assert reduction.assertions["compact_directional_source"]


def test_reducer_blocks_turn_and_prewarm_only_activity() -> None:
    reduction = reduce_codex_app_server(
        (
            _lifecycle("startup", LifecycleKind.START, 1),
            _lifecycle("startup", LifecycleKind.END, 4),
        ),
        (
            SourceObservation(
                "turn", _at(2), PRODUCER, "thread", "session_task.turn", "project-hash", "git"
            ),
            SourceObservation(
                "prewarm", _at(3), PRODUCER, "thread", "startup_prewarm", "project-hash", "git"
            ),
        ),
    )
    selection = reduction.selections[0]
    assert not selection.accepted
    assert dict(selection.gates)["directional_source"]
    assert not dict(selection.gates)["canonical_activity"]
    assert "startup_canonical_activity" in reduction.blocked_boundaries


def test_reducer_fails_closed_for_same_native_project_collision() -> None:
    reduction = reduce_codex_app_server(
        (
            _lifecycle("startup", LifecycleKind.START, 1),
            _lifecycle("startup", LifecycleKind.END, 4),
        ),
        (SourceObservation("source", _at(2), PRODUCER, "thread", "trace", "other-project", "git"),),
    )
    selection = reduction.selections[0]
    assert not selection.accepted
    assert not dict(selection.gates)["concurrent_project_isolation"]
    assert "startup_concurrent_project_isolation" in reduction.failed_boundaries


def test_reducer_accepts_canonical_non_git_project() -> None:
    reduction = reduce_codex_app_server(
        (
            _lifecycle("startup", LifecycleKind.START, 1, project_kind="non_git"),
            _lifecycle("startup", LifecycleKind.END, 4, project_kind="non_git"),
        ),
        (
            SourceObservation(
                "source", _at(2), PRODUCER, "thread", "trace", "project-hash", "non_git"
            ),
        ),
    )
    selection = reduction.selections[0]
    assert selection.accepted
    assert dict(selection.gates)["canonical_project_classification"]

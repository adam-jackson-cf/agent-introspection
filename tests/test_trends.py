import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from agent_introspection.trends import (
    Occurrence,
    TrendState,
    evaluate_findings,
    recompute_canonical_findings,
)


def occurrence(finding: str, task: str | None, when: datetime, sequence: int) -> Occurrence:
    return Occurrence(
        observation_id=f"o{sequence}",
        finding_id=finding,
        occurred_at_ns=int(when.timestamp() * 1_000_000_000),
        canonical_task_id=task,
    )


def test_trend_state_machine_uses_tasks_days_and_seven_day_window() -> None:
    now = datetime(2026, 7, 10, 12, tzinfo=UTC)
    rows = [
        occurrence("actionable", "t1", now - timedelta(days=2), 1),
        occurrence("actionable", "t2", now - timedelta(days=1), 2),
        occurrence("actionable", "t2", now, 3),
        occurrence("emerging", "t1", now - timedelta(hours=2), 4),
        occurrence("emerging", "t1", now - timedelta(hours=1), 5),
        occurrence("isolated", None, now, 6),
        occurrence("expired", "t1", now - timedelta(days=8), 7),
    ]
    states = {row.finding_id: row for row in evaluate_findings(rows, now=now)}
    assert states["actionable"].state is TrendState.ACTIONABLE
    assert states["actionable"].canonical_task_count == 2
    assert states["actionable"].local_day_count >= 2
    assert states["emerging"].state is TrendState.EMERGING
    assert states["isolated"].state is TrendState.ISOLATED
    assert "expired" not in states


def test_previously_actionable_absence_becomes_dormant_without_deletion() -> None:
    now = datetime(2026, 7, 10, 12, tzinfo=UTC)
    result = evaluate_findings([], now=now, previously_actionable={"known"})
    assert result[0].finding_id == "known"
    assert result[0].state is TrendState.DORMANT


def test_trends_require_timezone_aware_clock() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_findings([], now=datetime(2026, 7, 10, 12))


def test_canonical_recomputation_supersedes_late_project_membership_idempotently() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE project_identities (
            id TEXT PRIMARY KEY,
            identity_kind TEXT NOT NULL CHECK (identity_kind IN ('git', 'non_git')),
            canonical_path TEXT NOT NULL,
            git_common_dir TEXT,
            created_at TEXT NOT NULL,
            UNIQUE (identity_kind, canonical_path)
        ) STRICT;
        CREATE TABLE canonical_activities (
            id TEXT PRIMARY KEY,
            correlation_id TEXT NOT NULL,
            source_started_at_ns INTEGER NOT NULL,
            detector_id TEXT NOT NULL,
            detector_version INTEGER NOT NULL,
            normalization_version INTEGER NOT NULL,
            operation_kind TEXT NOT NULL,
            target_kind TEXT NOT NULL,
            normalized_target TEXT NOT NULL,
            normalized_failure_class TEXT NOT NULL
        ) STRICT;
        CREATE TABLE canonical_activity_versions (
            activity_id TEXT NOT NULL REFERENCES canonical_activities(id),
            version INTEGER NOT NULL,
            attribution_state TEXT NOT NULL CHECK (
                attribution_state IN ('resolved', 'unresolved')
            ),
            project_identity_id TEXT REFERENCES project_identities(id),
            PRIMARY KEY (activity_id, version),
            CHECK (
                attribution_state != 'resolved' OR project_identity_id IS NOT NULL
            )
        ) STRICT, WITHOUT ROWID;
        CREATE TABLE findings (
            id TEXT PRIMARY KEY,
            fingerprint TEXT UNIQUE NOT NULL CHECK (length(fingerprint) = 64),
            category TEXT NOT NULL,
            project_identity_id TEXT REFERENCES project_identities(id),
            trend_state TEXT NOT NULL CHECK (
                trend_state IN ('isolated', 'emerging', 'actionable', 'dormant')
            ),
            detector_id TEXT NOT NULL,
            detector_version INTEGER NOT NULL,
            first_seen_ns INTEGER NOT NULL,
            last_seen_ns INTEGER NOT NULL,
            occurrence_count INTEGER NOT NULL,
            canonical_task_count INTEGER NOT NULL,
            local_day_count INTEGER NOT NULL,
            entity_version INTEGER NOT NULL,
            is_active INTEGER NOT NULL CHECK (is_active IN (0, 1)),
            replaced_by_finding_id TEXT REFERENCES findings(id),
            updated_at TEXT NOT NULL,
            CHECK (
                (is_active = 1 AND replaced_by_finding_id IS NULL)
                OR (is_active = 0 AND replaced_by_finding_id IS NOT NULL)
            )
        ) STRICT;
        CREATE TABLE canonical_finding_membership (
            finding_id TEXT NOT NULL REFERENCES findings(id),
            activity_id TEXT NOT NULL REFERENCES canonical_activities(id),
            rationale TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (finding_id, activity_id)
        ) STRICT, WITHOUT ROWID;
        """
    )
    connection.executemany(
        "INSERT INTO project_identities VALUES (?, 'git', ?, NULL, '2026-07-10T12:00:00+00:00')",
        (("project-a", "/tmp/a"), ("project-b", "/tmp/b")),
    )
    connection.execute(
        """
        INSERT INTO canonical_activities
        VALUES (
            'a', 'task', 1783728000000000000, 'tool_failure', 1, 1,
            'tool', 'path', '/tmp/x', 'failure'
        )
        """
    )
    connection.execute(
        "INSERT INTO canonical_activity_versions VALUES ('a', 1, 'resolved', 'project-a')"
    )
    now = datetime(2026, 7, 10, 12, tzinfo=UTC)
    with connection:
        assert recompute_canonical_findings(connection, ["a"], now=now) == []
    first_fingerprint, first_id = connection.execute(
        "SELECT fingerprint, id FROM findings WHERE is_active = 1"
    ).fetchone()
    assert first_id == str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"agent-introspection:{first_fingerprint}")
    )

    connection.execute(
        "INSERT INTO canonical_activity_versions VALUES ('a', 2, 'resolved', 'project-b')"
    )
    with connection:
        assert recompute_canonical_findings(connection, ["a"], now=now) == []

    active_id = connection.execute("SELECT id FROM findings WHERE is_active = 1").fetchone()[0]
    assert connection.execute(
        "SELECT replaced_by_finding_id FROM findings WHERE id = ?", (first_id,)
    ).fetchone() == (active_id,)
    assert connection.execute(
        "SELECT COUNT(*) FROM canonical_finding_membership WHERE activity_id = 'a'"
    ).fetchone() == (2,)
    connection.execute("BEGIN")
    with connection:
        assert recompute_canonical_findings(connection, ["a"], now=now) == []

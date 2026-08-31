from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_introspection.attribution import (
    reconcile_activity,
    resolve_attribution,
    resolve_metric_attribution,
)
from agent_introspection.database import CanonicalActivity, CanonicalSourceMembership
from agent_introspection.migrations import apply_migrations
from agent_introspection.session_context import (
    SessionContextEvent,
    drain_inbox,
    parse_event,
    spool_event,
)

_PROJECT_ID = "b" * 64


def _connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    apply_migrations(connection, path)
    return connection


def _activity(moment: datetime, *, event_id: str = "event-1") -> CanonicalActivity:
    return CanonicalActivity(
        producer="claude-code",
        producer_surface="claude-code",
        correlation_id="session-1",
        source_started_at_ns=int(moment.timestamp() * 1_000_000_000),
        source_ended_at_ns=int(moment.timestamp() * 1_000_000_000),
        detector_id="test-detector",
        detector_version=1,
        normalization_version=1,
        source_membership=CanonicalSourceMembership(event_ids=(event_id,)),
        operation_kind="tool",
        target_kind="command",
        normalized_target="pytest",
        normalized_failure_class="failure",
        created_at=moment.isoformat(),
    )


def _context_event(root: Path, moment: datetime) -> SessionContextEvent:
    return parse_event(
        {
            "event_id": "a" * 64,
            "producer": "claude-code",
            "session_id": "session-1",
            "event_type": "session_start",
            "occurred_at": moment.isoformat(),
            "agent": {
                "project": {
                    "id": _PROJECT_ID,
                    "name": root.name,
                    "root": root.as_posix(),
                    "kind": "git",
                }
            },
        }
    )


def test_resolution_uses_one_half_open_context_interval_or_fixed_reason(tmp_path: Path) -> None:
    connection = _connection(tmp_path / "ledger.sqlite3")
    try:
        moment = datetime(2026, 7, 20, tzinfo=UTC)
        assert (
            resolve_attribution(
                connection,
                producer="claude-code",
                correlation_id="session-1",
                source_at=moment,
            ).reason_code
            == "missing_workspace"
        )

        root = tmp_path / "project"
        root.mkdir()
        inbox = tmp_path / "inbox"
        spool_event(_context_event(root, moment), directory=inbox)
        drain_inbox(connection, directory=inbox)

        resolved = resolve_attribution(
            connection,
            producer="claude-code",
            correlation_id="session-1",
            source_at=moment,
        )
        assert resolved.state == "resolved"
        assert resolved.project_id == _PROJECT_ID
        assert (
            resolve_attribution(
                connection,
                producer="claude-code",
                correlation_id="session-1",
                source_at=moment - timedelta(microseconds=1),
            ).reason_code
            == "missing_workspace"
        )
        skewed = resolve_attribution(
            connection,
            producer="claude-code",
            correlation_id="session-1",
            source_at=moment - timedelta(milliseconds=100),
            clock_skew_seconds=1,
        )
        assert skewed.state == "resolved"
        assert skewed.project_id == _PROJECT_ID
        with pytest.raises(ValueError, match="clock skew must not be negative"):
            resolve_attribution(
                connection,
                producer="claude-code",
                correlation_id="session-1",
                source_at=moment,
                clock_skew_seconds=-1,
            )
    finally:
        connection.close()


def test_metric_resolution_allows_only_bounded_post_end_delivery_delay(tmp_path: Path) -> None:
    connection = _connection(tmp_path / "ledger.sqlite3")
    try:
        moment = datetime(2026, 7, 20, tzinfo=UTC)
        root = tmp_path / "project"
        root.mkdir()
        inbox = tmp_path / "inbox"
        spool_event(_context_event(root, moment), directory=inbox)
        assert len(drain_inbox(connection, directory=inbox)) == 1
        spool_event(
            parse_event(
                {
                    "event_id": "c" * 64,
                    "producer": "claude-code",
                    "session_id": "session-1",
                    "event_type": "session_end",
                    "occurred_at": (moment + timedelta(seconds=1)).isoformat(),
                    "agent": {
                        "project": {
                            "id": _PROJECT_ID,
                            "name": root.name,
                            "root": root.as_posix(),
                            "kind": "git",
                        }
                    },
                }
            ),
            directory=inbox,
        )
        assert len(drain_inbox(connection, directory=inbox)) == 1

        source_at = moment + timedelta(seconds=6)
        assert (
            resolve_metric_attribution(
                connection,
                producer="claude-code",
                correlation_id="session-1",
                source_at=source_at,
                delivery_grace_seconds=4,
            ).reason_code
            == "missing_workspace"
        )
        resolved = resolve_metric_attribution(
            connection,
            producer="claude-code",
            correlation_id="session-1",
            source_at=source_at,
            delivery_grace_seconds=5,
        )
        assert resolved.state == "resolved"
        assert resolved.method == "session_context_delivery_grace"
        with pytest.raises(ValueError, match="metric delivery grace must not be negative"):
            resolve_metric_attribution(
                connection,
                producer="claude-code",
                correlation_id="session-1",
                source_at=source_at,
                delivery_grace_seconds=-1,
            )
    finally:
        connection.close()


def test_late_context_reconciles_one_activity_once_and_rolls_back_all_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = _connection(tmp_path / "ledger.sqlite3")
    try:
        moment = datetime(2026, 7, 20, tzinfo=UTC)
        activity = _activity(moment)
        first = reconcile_activity(connection, activity=activity, source_at=moment)
        assert first.version == 1
        assert first.version_inserted

        root = tmp_path / "project"
        root.mkdir()
        connection.execute(
            """
            INSERT INTO project_identities(
                id, identity_kind, canonical_path, git_common_dir, created_at
            ) VALUES (?, 'git', ?, NULL, ?)
            """,
            (_PROJECT_ID, root.as_posix(), moment.isoformat()),
        )
        connection.commit()
        inbox = tmp_path / "inbox"
        spool_event(_context_event(root, moment), directory=inbox)
        drain_inbox(connection, directory=inbox)

        second = reconcile_activity(connection, activity=activity, source_at=moment)
        assert (second.activity_id, second.version, second.version_inserted) == (
            first.activity_id,
            2,
            True,
        )
        duplicate = reconcile_activity(connection, activity=activity, source_at=moment)
        assert (duplicate.version, duplicate.version_inserted) == (2, False)
        assert connection.execute(
            "SELECT activity_version FROM canonical_recomputation_schedule "
            "WHERE activity_id = ? ORDER BY aggregate_kind",
            (activity.id,),
        ).fetchall() == [(1,), (2,), (1,), (2,)]

        outbox_count = connection.execute("SELECT COUNT(*) FROM otlp_outbox").fetchone()
        import agent_introspection.attribution as attribution

        monkeypatch.setattr(
            attribution,
            "_schedule_recomputation",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("schedule failed")),
        )
        changed = _activity(moment + timedelta(seconds=1), event_id="event-2")
        with pytest.raises(RuntimeError, match="schedule failed"):
            reconcile_activity(connection, activity=changed, source_at=moment)
        assert connection.execute(
            "SELECT COUNT(*) FROM canonical_activities WHERE id = ?", (changed.id,)
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM canonical_activity_versions WHERE activity_id = ?", (changed.id,)
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM canonical_activity_outbox_evidence WHERE activity_id = ?",
            (changed.id,),
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM canonical_recomputation_schedule WHERE activity_id = ?",
            (changed.id,),
        ).fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM otlp_outbox").fetchone() == outbox_count
    finally:
        connection.close()

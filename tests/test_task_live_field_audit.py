import sqlite3
from datetime import UTC, datetime, timedelta, timezone

from experiments.dashboard_prototype.contracts import ExperimentResult
from experiments.dashboard_prototype.experiment_live_common import LiveProofRequest
from experiments.dashboard_prototype.task_field_audit import (
    REQUIRED_TASK_FIELDS,
    FieldAuthorityState,
)
from experiments.dashboard_prototype.task_live_field_audit import extract

START = datetime(2026, 9, 2, 12, tzinfo=UTC)


def _request() -> LiveProofRequest:
    return LiveProofRequest("task-field-audit", START, START + timedelta(minutes=1))


def _database(
    *, producers: tuple[str, ...] = ("omp", "codex-cli", "codex-app-server")
) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE canonical_activities (
            id TEXT, producer TEXT, producer_surface TEXT, correlation_id TEXT,
            source_started_at_ns INTEGER, source_ended_at_ns INTEGER,
            source_membership_hash TEXT, source_membership_json TEXT,
            operation_kind TEXT, target_kind TEXT, normalized_target TEXT,
            normalized_failure_class TEXT
        );
        CREATE TABLE canonical_activity_versions (
            activity_id TEXT, version INTEGER, attribution_state TEXT,
            project_identity_id TEXT
        );
        """
    )
    for ordinal, producer in enumerate(producers):
        activity_id = f"activity-{ordinal}"
        ended = START + timedelta(seconds=ordinal + 1)
        connection.execute(
            "INSERT INTO canonical_activities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                activity_id,
                producer,
                producer,
                f"private-session-{ordinal}",
                int(START.timestamp() * 1_000_000_000),
                int(ended.timestamp() * 1_000_000_000),
                "a" * 64,
                '{"event_ids":["private-event"]}',
                "exec",
                "path",
                "/private/target",
                "private-failure",
            ),
        )
        connection.execute(
            "INSERT INTO canonical_activity_versions VALUES (?, ?, ?, ?)",
            (activity_id, 1, "resolved", "private-project"),
        )
        connection.execute(
            "INSERT INTO canonical_activity_versions VALUES (?, ?, ?, ?)",
            (activity_id, 2, "unresolved", None),
        )
    return connection


def test_live_matrix_is_blocked_complete_and_private() -> None:
    evidence = extract(_database(), _request())
    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert len(evidence.primitives) == 3 * len(REQUIRED_TASK_FIELDS)
    assert sum(value["classification_count"] for value in evidence.remote_oracle.values()) == len(
        evidence.primitives
    )
    assert all(row.source_time == _request().end for row in evidence.primitives)
    serialized = repr(evidence)
    assert "private-session" not in serialized
    assert "private-event" not in serialized
    assert "private-project" not in serialized
    assert "/private/target" not in serialized
    assert "private-failure" not in serialized


def test_latest_project_version_is_classified_without_exposing_resolved_state() -> None:
    evidence = extract(_database(), _request())
    project_rows = [
        row for row in evidence.primitives if row.dimensions["field"] == "project_identity_state"
    ]
    assert {row.dimensions["authority_state"] for row in project_rows} == {"authoritative"}
    assert all("project" not in row.dimensions for row in project_rows)


def test_missing_in_window_producer_is_ambiguous_not_unsupported_or_zero() -> None:
    evidence = extract(_database(producers=("omp", "codex-cli")), _request())
    app_rows = [
        row
        for row in evidence.primitives
        if row.dimensions["producer"] == "codex-app-server"
        and row.dimensions["field"] == "native_session_id"
    ]
    assert app_rows[0].dimensions["authority_state"] == FieldAuthorityState.AMBIGUOUS.value


def test_equivalent_non_utc_range_has_exact_utc_membership() -> None:
    offset = timezone(timedelta(hours=5))
    request = LiveProofRequest(
        "task-field-audit-offset",
        START.astimezone(offset),
        (START + timedelta(minutes=1)).astimezone(offset),
    )
    evidence = extract(_database(), request)
    session_rows = [
        row for row in evidence.primitives if row.dimensions["field"] == "native_session_id"
    ]
    assert {row.dimensions["authority_state"] for row in session_rows} == {"authoritative"}


def test_missing_schema_marks_every_field_absent() -> None:
    evidence = extract(sqlite3.connect(":memory:"), _request())
    assert {row.dimensions["authority_state"] for row in evidence.primitives} == {"absent"}

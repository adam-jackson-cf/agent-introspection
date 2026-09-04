import sqlite3
from datetime import UTC, datetime, timedelta, timezone

from experiments.dashboard_prototype.contracts import ExperimentResult
from experiments.dashboard_prototype.experiment_live_common import LiveProofRequest
from experiments.dashboard_prototype.request_field_audit import (
    REQUIRED_REQUEST_FIELDS,
    FieldAuthorityState,
)
from experiments.dashboard_prototype.request_live_field_audit import extract

START = datetime(2026, 9, 2, 12, tzinfo=UTC)


def _request() -> LiveProofRequest:
    return LiveProofRequest("request-audit", START, START + timedelta(minutes=1))


def _database(
    *, producers: tuple[str, ...] = ("omp", "codex-cli", "codex-app-server")
) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE session_context_events ("
        "event_id TEXT, producer TEXT, session_id TEXT, event_type TEXT, "
        "occurred_at TEXT, project_id TEXT)"
    )
    for ordinal, producer in enumerate(producers):
        connection.execute(
            "INSERT INTO session_context_events VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"event-{ordinal}",
                producer,
                f"session-{ordinal}",
                "session_context",
                (START + timedelta(seconds=ordinal + 1)).isoformat(),
                f"project-{ordinal}",
            ),
        )
    return connection


def test_current_live_schema_emits_complete_scalar_conserving_matrix() -> None:
    evidence = extract(_database(), _request())
    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert len(evidence.primitives) == 3 * len(REQUIRED_REQUEST_FIELDS)
    assert sum(value["classification_count"] for value in evidence.remote_oracle.values()) == len(
        evidence.primitives
    )
    assert all(row.source_time == _request().end for row in evidence.primitives)
    assert all("session" not in row.dimensions for row in evidence.primitives)


def test_missing_in_window_producer_is_ambiguous_not_unsupported_or_zero() -> None:
    evidence = extract(_database(producers=("omp", "codex-cli")), _request())
    app_rows = [
        row
        for row in evidence.primitives
        if row.dimensions["producer"] == "codex-app-server"
        and row.dimensions["field"] == "project_id"
    ]
    assert app_rows[0].dimensions["authority_state"] == FieldAuthorityState.AMBIGUOUS.value


def test_equivalent_non_utc_bounds_use_canonical_utc_filtering() -> None:
    offset = timezone(timedelta(hours=5))
    request = LiveProofRequest(
        "request-audit-offset",
        START.astimezone(offset),
        (START + timedelta(minutes=1)).astimezone(offset),
    )
    evidence = extract(_database(), request)
    common = [row for row in evidence.primitives if row.dimensions["field"] == "native_session_id"]
    assert {row.dimensions["authority_state"] for row in common} == {"authoritative"}


def test_missing_schema_is_unsupported_for_common_boundary() -> None:
    evidence = extract(sqlite3.connect(":memory:"), _request())
    common = [row for row in evidence.primitives if row.dimensions["field"] == "project_id"]
    assert {row.dimensions["authority_state"] for row in common} == {"unsupported"}

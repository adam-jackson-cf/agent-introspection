from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from experiments.dashboard_prototype.attribution_live_baseline import (
    extract,
    parse_retained_authorities,
    row_evidence_inputs,
)
from experiments.dashboard_prototype.attribution_live_common import LiveProofRequest
from experiments.dashboard_prototype.contracts import ExperimentResult

_FIXTURE = Path("tests/fixtures/producer_identity_proofs.json")


def test_retained_fixture_keeps_only_supported_directional_authority() -> None:
    authorities = parse_retained_authorities(_FIXTURE)
    assert [authority.producer for authority in authorities] == ["codex-cli", "omp"]
    codex = next(authority for authority in authorities if authority.producer == "codex-cli")
    omp = next(authority for authority in authorities if authority.producer == "omp")
    assert codex.capabilities["end"].value == "not_exposed"
    assert omp.capabilities["workspace_change"].value == "not_exposed"
    assert all(len(authority.evidence_id) == 16 for authority in authorities)


def test_missing_current_activity_tables_blocks_only_current_boundary() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        request = LiveProofRequest(
            "attribution-1", datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 8, 2, tzinfo=UTC)
        )
        evidence = extract(connection, request, retained_fixture=_FIXTURE)
        assert evidence.proof.result is ExperimentResult.BLOCKED
        assert evidence.proof.blocked_boundaries == ("current_source_membership",)
        assert evidence.proof.metrics["p5.omp.omp.fresh"] == "passed"
        assert evidence.primitives == ()
    finally:
        connection.close()


def _event_id(activity_id: str, version: int) -> str:
    return hashlib.sha256(
        "\x1f".join(
            (activity_id, str(version), "2", "introspection.activity.version.recorded")
        ).encode()
    ).hexdigest()


def _insert_activity_envelope(
    connection: sqlite3.Connection, activity_id: str, version: int
) -> None:
    producer, surface, session_id, source_time_ns, state, project_id, method, reason = (
        connection.execute(
            """
            SELECT activity.producer, activity.producer_surface, activity.correlation_id,
                   activity.source_ended_at_ns, version.attribution_state,
                   version.project_identity_id, version.attribution_method, version.reason_code
            FROM canonical_activities AS activity
            JOIN canonical_activity_versions AS version ON version.activity_id = activity.id
            WHERE activity.id = ? AND version.version = ?
            """,
            (activity_id, version),
        ).fetchone()
    )
    event_id = _event_id(activity_id, version)
    payload = {
        "event.id": event_id,
        "event.scope": "canonical-activity",
        "event.name": "introspection.activity.version.recorded",
        "activity.id": activity_id,
        "activity.version": version,
        "activity.payload_schema_version": 2,
        "timestamp_ns": source_time_ns,
        "activity.producer": producer,
        "activity.producer_surface": surface,
        "activity.correlation_id": session_id,
        "activity.attribution.state": state,
        "activity.attribution.method": method,
        "agent.project.id": project_id if project_id is not None else "unresolved",
    }
    if project_id is None:
        payload["activity.attribution.reason_code"] = reason
    else:
        payload["activity.attribution.project_identity_id"] = project_id
    connection.execute(
        "INSERT INTO otlp_outbox VALUES (?, ?)",
        (event_id, json.dumps(payload, sort_keys=True)),
    )
    connection.execute(
        "INSERT INTO canonical_activity_outbox_evidence VALUES (?, ?, ?, ?, ?)",
        (activity_id, version, 2, "introspection.activity.version.recorded", event_id),
    )


def _database(*, producer: str = "omp") -> tuple[sqlite3.Connection, datetime, int]:
    connection = sqlite3.connect(":memory:")
    connection.executescript("""
        CREATE TABLE canonical_activities (
            id TEXT, producer TEXT, producer_surface TEXT, correlation_id TEXT,
            source_started_at_ns INTEGER, source_ended_at_ns INTEGER
        );
        CREATE TABLE canonical_activity_versions (
            activity_id TEXT, version INTEGER, attribution_state TEXT,
            project_identity_id TEXT, attribution_method TEXT, reason_code TEXT
        );
        CREATE TABLE canonical_activity_outbox_evidence (
            activity_id TEXT, activity_version INTEGER, payload_schema_version INTEGER,
            event_name TEXT, event_id TEXT
        );
        CREATE TABLE otlp_outbox (event_id TEXT, payload_json TEXT);
        CREATE TABLE session_context_intervals (
            producer TEXT, session_id TEXT, started_at TEXT, ended_at TEXT,
            event_id TEXT, end_event_id TEXT, project_id TEXT
        );
        CREATE TABLE session_context_events (
            event_id TEXT, producer TEXT, session_id TEXT, event_type TEXT, occurred_at TEXT,
            project_id TEXT
        );
        CREATE TABLE session_context_event_supersessions (
            original_event_id TEXT, replacement_event_id TEXT
        );
        CREATE TABLE source_session_current (
            native_producer TEXT, native_session_id TEXT, source_id TEXT,
            source_timestamp TEXT, source_timestamp_ns TEXT
        );
    """)
    start = datetime(2026, 8, 1, tzinfo=UTC)
    at = start + timedelta(seconds=1)
    source_time_ns = int(at.timestamp() * 1_000_000_000)
    connection.execute(
        "INSERT INTO canonical_activities VALUES (?, ?, ?, ?, ?, ?)",
        (
            "activity",
            producer,
            producer,
            "session",
            int((start - timedelta(seconds=1)).timestamp() * 1_000_000_000),
            source_time_ns,
        ),
    )
    connection.executemany(
        "INSERT INTO canonical_activity_versions VALUES (?, ?, ?, ?, ?, ?)",
        (
            ("activity", 1, "unresolved", None, "none", "missing_context"),
            ("activity", 2, "resolved", "a" * 64, "exact_context", None),
        ),
    )
    _insert_activity_envelope(connection, "activity", 1)
    _insert_activity_envelope(connection, "activity", 2)
    if producer == "omp":
        connection.execute(
            "INSERT INTO session_context_intervals VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("omp", "session", start.isoformat(), None, "event", None, "a" * 64),
        )
        connection.execute(
            "INSERT INTO session_context_events VALUES (?, ?, ?, ?, ?, ?)",
            ("event", "omp", "session", "session_start", start.isoformat(), "a" * 64),
        )
        connection.execute(
            "INSERT INTO source_session_current VALUES (?, ?, ?, ?, ?)",
            ("omp", "session", "source", at.isoformat(), str(source_time_ns)),
        )
    return connection, start, source_time_ns


def test_live_extractor_selects_latest_version_with_source_time_bounds() -> None:
    connection, start, _ = _database()
    try:
        evidence = extract(
            connection,
            LiveProofRequest("attribution-2", start, start + timedelta(minutes=1)),
            retained_fixture=_FIXTURE,
        )
        assert evidence.proof.result is ExperimentResult.PROVEN
        assert evidence.proof.metrics["p7_eligible"] == 1
        assert evidence.proof.metrics["p7_attributed"] == 1
        rows = row_evidence_inputs(
            connection,
            LiveProofRequest("attribution-2", start, start + timedelta(minutes=1)),
        )
        assert {(row.row_id, row.producer) for row in rows} == {
            ("A07", "omp"),
            ("A08", "omp"),
            ("A09", "omp"),
        }
        assert {row.native_session_id for row in rows} == {"session"}
        assert len(evidence.primitives) == 2

        interval_start = start + timedelta(milliseconds=500)
        end = start + timedelta(minutes=1)
        start_ns = int(start.timestamp()) * 1_000_000_000
        end_ns = start_ns + 60_000_000_000
        connection.execute(
            "UPDATE session_context_events SET occurred_at = ?", (interval_start.isoformat(),)
        )
        connection.execute(
            "UPDATE session_context_intervals SET started_at = ?", (interval_start.isoformat(),)
        )
        connection.executemany(
            "INSERT INTO source_session_current VALUES (?, ?, ?, ?, ?)",
            (
                ("omp", "session", "first", start.isoformat(), str(start_ns + 1)),
                ("omp", "lower", "lower", start.isoformat(), str(start_ns)),
                ("omp", "upper", "upper", end.isoformat(), str(end_ns)),
                ("omp", "after", "after", end.isoformat(), str(end_ns + 1)),
            ),
        )
        bounded = extract(
            connection, LiveProofRequest("attribution-2", start, end), retained_fixture=_FIXTURE
        )
        expected_p5 = {
            "source_sessions": 2,
            "source_with_lifecycle": 0,
            "lifecycle_sessions": 1,
            "lifecycle_with_source": 1,
        }
        assert bounded.remote_oracle["p5.omp.omp"] == expected_p5

        connection.execute(
            "UPDATE source_session_current SET source_timestamp_ns = NULL WHERE source_id = 'first'"
        )
        unknown = extract(
            connection, LiveProofRequest("attribution-2", start, end), retained_fixture=_FIXTURE
        )
        assert "p5.omp.omp" not in unknown.remote_oracle
        assert unknown.remote_oracle["p7"]["eligible"] == 1

        connection.execute(
            "UPDATE source_session_current SET source_timestamp_ns = ? WHERE source_id = 'first'",
            (str(start_ns + 1),),
        )
        connection.execute(
            "DELETE FROM canonical_activity_outbox_evidence WHERE activity_version = 2"
        )
        partial = extract(
            connection, LiveProofRequest("attribution-2", start, end), retained_fixture=_FIXTURE
        )
        assert partial.proof.result is ExperimentResult.BLOCKED
        assert partial.remote_oracle["p5.omp.omp"] == expected_p5
        assert "p7" not in partial.remote_oracle
        assert "p8" not in partial.remote_oracle
    finally:
        connection.close()


def test_activity_history_requires_an_immutable_envelope_per_version() -> None:
    connection, start, _ = _database()
    try:
        connection.execute(
            "DELETE FROM canonical_activity_outbox_evidence WHERE activity_version = 1"
        )
        evidence = extract(
            connection,
            LiveProofRequest("attribution-history", start, start + timedelta(minutes=1)),
            retained_fixture=_FIXTURE,
        )
        assert evidence.proof.result is ExperimentResult.BLOCKED
        assert all(row.dimensions["primitive_kind"] == "p5" for row in evidence.primitives)
        assert evidence.remote_oracle == {
            "p5.omp.omp": {
                "source_sessions": 1,
                "source_with_lifecycle": 1,
                "lifecycle_sessions": 0,
                "lifecycle_with_source": 0,
            }
        }
    finally:
        connection.close()


def test_codex_cli_uses_unsuperseded_same_project_points_before_intervals() -> None:
    connection, start, _ = _database(producer="codex-cli")
    try:
        connection.executemany(
            "INSERT INTO session_context_events VALUES (?, ?, ?, ?, ?, ?)",
            (
                ("point-1", "codex-cli", "session", "session_context", start.isoformat(), "a" * 64),
                (
                    "point-2",
                    "codex-cli",
                    "session",
                    "session_context",
                    (start + timedelta(seconds=1)).isoformat(),
                    "a" * 64,
                ),
                (
                    "superseded-point",
                    "codex-cli",
                    "session",
                    "session_context",
                    (start + timedelta(seconds=2)).isoformat(),
                    "b" * 64,
                ),
            ),
        )
        connection.execute(
            "INSERT INTO session_context_event_supersessions VALUES (?, ?)",
            ("superseded-point", "point-1"),
        )
        connection.execute(
            "INSERT INTO session_context_intervals VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "codex-cli",
                "session",
                start.isoformat(),
                None,
                "unsupported-cli-interval",
                None,
                "b" * 64,
            ),
        )
        evidence = extract(
            connection,
            LiveProofRequest("attribution-cli", start, start + timedelta(minutes=1)),
            retained_fixture=_FIXTURE,
        )
        assert evidence.proof.metrics["p7_attributed"] == 1
    finally:
        connection.close()


def test_codex_cli_rejects_multiple_unsuperseded_point_projects() -> None:
    connection, start, _ = _database(producer="codex-cli")
    try:
        connection.executemany(
            "INSERT INTO session_context_events VALUES (?, ?, ?, ?, ?, ?)",
            (
                ("point-a", "codex-cli", "session", "session_context", start.isoformat(), "a" * 64),
                ("point-b", "codex-cli", "session", "session_context", start.isoformat(), "b" * 64),
            ),
        )
        evidence = extract(
            connection,
            LiveProofRequest("attribution-cli-conflict", start, start + timedelta(minutes=1)),
            retained_fixture=_FIXTURE,
        )
        assert evidence.proof.result is ExperimentResult.BLOCKED
        assert evidence.primitives == ()
    finally:
        connection.close()


def test_superseded_lifecycle_rows_do_not_supply_authority() -> None:
    connection, start, _ = _database()
    try:
        connection.execute(
            "INSERT INTO session_context_event_supersessions VALUES (?, ?)",
            ("event", "replacement"),
        )
        evidence = extract(
            connection,
            LiveProofRequest("attribution-superseded", start, start + timedelta(minutes=1)),
            retained_fixture=_FIXTURE,
        )
        assert evidence.proof.result is ExperimentResult.BLOCKED
        assert all(row.dimensions["primitive_kind"] == "p5" for row in evidence.primitives)
        assert evidence.remote_oracle == {
            "p5.omp.omp": {
                "source_sessions": 1,
                "source_with_lifecycle": 0,
                "lifecycle_sessions": 0,
                "lifecycle_with_source": 0,
            }
        }
    finally:
        connection.close()

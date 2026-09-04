from __future__ import annotations

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


def test_live_extractor_selects_latest_version_with_source_time_bounds() -> None:
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
        CREATE TABLE session_context_intervals (
            producer TEXT, session_id TEXT, started_at TEXT, ended_at TEXT,
            event_id TEXT, project_id TEXT
        );
        CREATE TABLE source_session_current (
            native_producer TEXT, native_session_id TEXT, source_timestamp TEXT
        );
    """)
    start = datetime(2026, 8, 1, tzinfo=UTC)
    at = start + timedelta(seconds=1)
    connection.execute(
        "INSERT INTO canonical_activities VALUES (?, ?, ?, ?, ?, ?)",
        (
            "activity",
            "omp",
            "omp",
            "session",
            int((start - timedelta(seconds=1)).timestamp() * 1_000_000_000),
            int(at.timestamp() * 1_000_000_000),
        ),
    )
    connection.executemany(
        "INSERT INTO canonical_activity_versions VALUES (?, ?, ?, ?, ?, ?)",
        (
            ("activity", 1, "unresolved", None, "none", "missing_context"),
            ("activity", 2, "resolved", "a" * 64, "exact_context", None),
        ),
    )
    connection.execute(
        "INSERT INTO session_context_intervals VALUES (?, ?, ?, ?, ?, ?)",
        ("omp", "session", start.isoformat(), None, "event", "a" * 64),
    )
    connection.execute(
        "INSERT INTO source_session_current VALUES (?, ?, ?)",
        ("omp", "session", at.isoformat()),
    )
    try:
        evidence = extract(
            connection,
            LiveProofRequest("attribution-2", start, start + timedelta(minutes=1)),
            retained_fixture=_FIXTURE,
        )
        assert evidence.proof.result is ExperimentResult.PROVEN
        assert evidence.proof.metrics["p7_eligible"] == 1
        assert evidence.proof.metrics["p7_attributed"] == 1
        reconstructed = {
            "p7": {
                "eligible": sum(
                    row.dimensions["primitive_kind"] == "p7_p8" for row in evidence.primitives
                ),
                "attributed": sum(
                    row.dimensions.get("outcome") == "attributed" for row in evidence.primitives
                ),
                "unresolved": sum(
                    row.dimensions.get("outcome") == "unresolved" for row in evidence.primitives
                ),
                "distinct_projects": len(
                    {
                        row.dimensions["project_digest"]
                        for row in evidence.primitives
                        if row.dimensions.get("primitive_kind") == "p7_p8"
                        and row.dimensions["project_digest"] != "none"
                    }
                ),
            },
            "p5.omp.omp": {
                "source_sessions": sum(
                    row.dimensions.get("direction") == "source_to_lifecycle"
                    for row in evidence.primitives
                ),
                "source_with_lifecycle": sum(
                    row.dimensions.get("direction") == "source_to_lifecycle"
                    and row.dimensions.get("matched") is True
                    for row in evidence.primitives
                ),
                "lifecycle_sessions": sum(
                    row.dimensions.get("direction") == "lifecycle_to_source"
                    for row in evidence.primitives
                ),
                "lifecycle_with_source": sum(
                    row.dimensions.get("direction") == "lifecycle_to_source"
                    and row.dimensions.get("matched") is True
                    for row in evidence.primitives
                ),
            },
        }
        assert evidence.remote_oracle["p7"] == reconstructed["p7"]
        assert evidence.remote_oracle["p5.omp.omp"] == reconstructed["p5.omp.omp"]
        assert evidence.remote_oracle["p7.omp.omp"] == reconstructed["p7"]
        assert evidence.remote_oracle["p8"]["total"] == reconstructed["p7"]["eligible"]
        rows = row_evidence_inputs(
            connection,
            LiveProofRequest("attribution-2", start, start + timedelta(minutes=1)),
        )
        assert {(row.row_id, row.producer) for row in rows} == {
            ("A07", "omp"),
            ("A08", "omp"),
            ("A09", "omp"),
        }
        assert {len(row.native_session_id) for row in rows} == {16}
        assert len(evidence.primitives) == 2
    finally:
        connection.close()

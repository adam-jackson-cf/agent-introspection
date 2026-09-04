from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from experiments.dashboard_prototype.attribution_claude_boundary import (
    build_claude_boundary_proof,
    extract_claude_boundary,
    load_claude_boundary_evidence,
)
from experiments.dashboard_prototype.contracts import ExperimentResult
from experiments.dashboard_prototype.experiment_live_common import LiveProofRequest

_REOPENING_CONDITION = "hook session ID = local artifact session ID = OTEL session ID"


def _fixture(path: Path, **record_overrides: object) -> Path:
    record = {
        "producer": "claude-code",
        "missing_equality_boundary": _REOPENING_CONDITION,
        "source_ingestion_enabled": False,
        **record_overrides,
    }
    path.write_text(json.dumps({"unsupported": [record]}), encoding="utf-8")
    return path


def _request() -> LiveProofRequest:
    start = datetime(2026, 9, 1, tzinfo=UTC)
    return LiveProofRequest("claude-boundary-run", start, start + timedelta(minutes=1))


def test_retained_claude_boundary_is_redacted_immutable_and_blocked(tmp_path: Path) -> None:
    fixture = _fixture(
        tmp_path / "identity.json",
        observed_hook_session_id="hook-raw-id",
        explicit_session_id="artifact-raw-id",
        command_argv_without_prompt=["claude", "--session-id", "artifact-raw-id"],
    )

    evidence = load_claude_boundary_evidence(fixture)
    proof = build_claude_boundary_proof("claude-boundary-run", evidence)
    live = extract_claude_boundary(fixture, _request())

    assert evidence.producer == "claude-code"
    assert evidence.reopening_condition == _REOPENING_CONDITION
    assert evidence.ingestion_enabled is False
    assert len(evidence.evidence_id) == 16
    with pytest.raises(FrozenInstanceError):
        evidence.ingestion_enabled = True  # type: ignore[misc]
    assert proof.result is ExperimentResult.BLOCKED
    assert proof.metrics["supported_denominator_population"] == 0
    assert proof.metrics["remote_primitive_count"] == 0
    assert proof.proposal == _REOPENING_CONDITION
    assert live.primitives == ()
    assert live.remote_query_id is None
    emitted = proof.canonical_json()
    for forbidden in ("hook-raw-id", "artifact-raw-id", "--session-id", "argv"):
        assert forbidden not in emitted


@pytest.mark.parametrize(
    "overrides",
    [
        {"missing_equality_boundary": "not the three-way equality"},
        {"source_ingestion_enabled": True},
        {"fresh_installed_authority": True},
    ],
)
def test_malformed_or_contradictory_claude_fixture_fails_closed(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    fixture = _fixture(tmp_path / "identity.json", **overrides)

    with pytest.raises(ValueError, match=r"fixture|authority"):
        load_claude_boundary_evidence(fixture)


def test_duplicate_claude_records_fail_closed(tmp_path: Path) -> None:
    fixture = tmp_path / "identity.json"
    fixture.write_text(
        json.dumps(
            {
                "unsupported": [
                    {
                        "producer": "claude-code",
                        "missing_equality_boundary": _REOPENING_CONDITION,
                        "source_ingestion_enabled": False,
                    },
                    {
                        "producer": "claude-code",
                        "missing_equality_boundary": _REOPENING_CONDITION,
                        "source_ingestion_enabled": False,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="exactly one"):
        load_claude_boundary_evidence(fixture)

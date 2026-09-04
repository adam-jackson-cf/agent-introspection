import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from experiments.dashboard_prototype.contracts import ExperimentResult
from experiments.dashboard_prototype.pipeline_integrity import IntegrityInvariant
from experiments.dashboard_prototype.pipeline_live_common import LiveProofRequest
from experiments.dashboard_prototype.pipeline_live_integrity import extract

START = datetime(2026, 9, 1, 12, tzinfo=UTC)
END = START + timedelta(hours=1)


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE canonical_rejections (
            id TEXT PRIMARY KEY,
            producer TEXT NOT NULL,
            producer_surface TEXT NOT NULL,
            correlation_id TEXT,
            lifecycle_event TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            source_adapter TEXT NOT NULL,
            source_provenance TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    return connection


@dataclass(frozen=True)
class _RejectionRow:
    identifier: str
    producer: str = "codex-cli"
    surface: str = "codex-cli"
    occurred_at: datetime = END
    reason: str = "missing_correlation_id"
    adapter: str = "notify"


def _insert(connection: sqlite3.Connection, row: _RejectionRow) -> None:
    connection.execute(
        """
        INSERT INTO canonical_rejections (
            id, producer, producer_surface, correlation_id, lifecycle_event,
            occurred_at, reason_code, source_adapter, source_provenance, created_at
        ) VALUES (?, ?, ?, 'private-correlation', 'session_start', ?, ?, ?, ?, ?)
        """,
        (
            row.identifier,
            row.producer,
            row.surface,
            row.occurred_at.isoformat(),
            row.reason,
            row.adapter,
            '{"private":"payload"}',
            row.occurred_at.isoformat(),
        ),
    )


def _request() -> LiveProofRequest:
    return LiveProofRequest("run-1", START, END)


def test_extracts_redacted_mapped_rows_at_canonical_boundaries() -> None:
    connection = _connection()
    _insert(connection, _RejectionRow("at-start", occurred_at=START))
    _insert(connection, _RejectionRow("at-end", occurred_at=END, reason="duplicate_conflict"))
    _insert(
        connection,
        _RejectionRow("claude", producer="claude-code", occurred_at=END),
    )

    evidence = extract(connection, _request())

    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.evidence_ids
    assert len(evidence.proof.evidence_ids) == 1
    assert evidence.proof.metrics == {}
    assert "durable-integrity-failure population" in evidence.proof.blocked_boundaries
    assert evidence.proof.assertions["durable_integrity_failure_population_authoritative"] is False
    incident = evidence.proof.evidence_ids[0]
    assert incident.startswith("p11-")
    assert "at-end" not in repr(evidence)
    assert "private-correlation" not in repr(evidence)
    assert "payload" not in repr(evidence)
    assert evidence.primitives[0].dimensions["withheld"] == "true"
    assert evidence.primitives[0].measures == {"incident_count": 1}


@pytest.mark.parametrize(
    ("reason", "invariant"),
    [
        ("missing_workspace", IntegrityInvariant.CANONICAL_LIFECYCLE_CONTEXT_REJECTION),
        ("conflicting_correlation_id", IntegrityInvariant.CONFLICTING_IDENTITY),
        ("duplicate_conflict", IntegrityInvariant.DETERMINISTIC_ID_CONFLICT),
        ("out_of_order_event", IntegrityInvariant.VERSION_GAP),
    ],
)
def test_maps_closed_reason_classes(reason: str, invariant: IntegrityInvariant) -> None:
    connection = _connection()
    _insert(connection, _RejectionRow("durable-1", reason=reason))

    evidence = extract(connection, _request())

    assert evidence.proof.evidence_ids[0].startswith("p11-")
    expected_metrics = (
        {f"p11.{invariant.value}.codex-cli.notify": 1}
        if invariant is IntegrityInvariant.CANONICAL_LIFECYCLE_CONTEXT_REJECTION
        else {}
    )
    assert evidence.proof.metrics == expected_metrics


def test_unknown_reason_and_unsafe_adapter_fail_closed() -> None:
    unknown = _connection()
    _insert(unknown, _RejectionRow("unknown-1", reason="future_reason"))
    with pytest.raises(ValueError, match="unregistered"):
        extract(unknown, _request())

    unsafe = _connection()
    _insert(unsafe, _RejectionRow("unsafe-1", adapter="adapter/with/path"))
    with pytest.raises(ValueError, match="unsafe"):
        extract(unsafe, _request())


def test_empty_selection_has_explicit_zero_population_primitive() -> None:
    evidence = extract(_connection(), _request())

    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.primitives[0].dimensions == {
        "metric": "zero_population_marker",
        "withheld": "false",
    }
    assert evidence.primitives[0].measures == {"incident_count": 0}


def test_corrupt_cohort_is_withheld_and_ids_are_deterministic() -> None:
    first = _connection()
    second = _connection()
    rows = [
        _RejectionRow("clean", producer="omp", surface="omp", reason="missing_workspace"),
        _RejectionRow(
            "corrupt",
            producer="codex-cli",
            surface="codex-cli",
            reason="duplicate_conflict",
        ),
    ]
    for row in rows:
        _insert(first, row)
    for row in reversed(rows):
        _insert(second, row)

    first_evidence = extract(first, _request())
    second_evidence = extract(second, _request())

    assert first_evidence.proof.evidence_ids == second_evidence.proof.evidence_ids
    assert first_evidence.proof.metrics == {
        "p11.canonical-lifecycle-context-rejection.omp.notify": 1
    }
    assert len(first_evidence.primitives) == 2
    primitives = {
        (primitive.dimensions["metric"], primitive.dimensions["withheld"])
        for primitive in first_evidence.primitives
    }
    assert primitives == {
        ("p11.canonical-lifecycle-context-rejection.omp.notify", "false"),
        ("p11.deterministic-id-conflict.codex-cli.notify", "true"),
    }

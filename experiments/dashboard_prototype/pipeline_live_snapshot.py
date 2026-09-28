"""Read-only adapter for the captured native pipeline snapshot boundary."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import datetime

from experiments.dashboard_prototype.contracts import EvidenceProvenance
from experiments.dashboard_prototype.pipeline_common import PipelineExperimentProof
from experiments.dashboard_prototype.pipeline_live_common import (
    LiveExperimentEvidence,
    LiveProofRequest,
)
from experiments.dashboard_prototype.pipeline_snapshot import (
    CompletedScanSnapshotCandidate,
    PipelineSnapshotProofInput,
    SnapshotPopulationCounts,
    build_pipeline_snapshot_proof,
)

_NATIVE_SNAPSHOT_COLUMNS = frozenset({"event_id", "scan_run_id"})
_EXECUTION_INPUT_COLUMNS = frozenset(
    {
        "scan_run_id",
        "monotonic_started",
        "monotonic_finished",
        "logs_json",
        "traces_json",
        "context_events_json",
        "canonical_activities_json",
    }
)


def extract(connection: sqlite3.Connection, request: LiveProofRequest) -> LiveExperimentEvidence:
    """Read captured native snapshots without reinterpreting absent dashboard fields."""
    columns = _columns(connection)
    scans = _scan_rows(connection, request, columns)
    candidates = tuple(_candidate(row) for row in scans)
    proof = build_pipeline_snapshot_proof(
        run_id=request.run_id,
        provenance=EvidenceProvenance.FRESH_REAL,
        source_boundary=request.source_boundary,
        proof_input=PipelineSnapshotProofInput(candidates, request.start, request.end, None),
    )
    proof = _with_native_observation_metrics(proof, candidates)
    return LiveExperimentEvidence(proof, (), None)


def _columns(connection: sqlite3.Connection) -> dict[str, frozenset[str]]:
    names = ("scan_runs", "native_pipeline_snapshots", "scan_execution_inputs")
    existing = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    return {
        name: frozenset(str(row[1]) for row in connection.execute(f"PRAGMA table_info({name})"))
        for name in names
        if name in existing
    }


def _scan_rows(
    connection: sqlite3.Connection,
    request: LiveProofRequest,
    columns: dict[str, frozenset[str]],
) -> tuple[sqlite3.Row, ...]:
    required_scan_columns = {"id", "status", "completed_at"}
    if not required_scan_columns <= columns.get("scan_runs", frozenset()):
        return ()
    if not columns.get("native_pipeline_snapshots", frozenset()) >= _NATIVE_SNAPSHOT_COLUMNS:
        return ()
    if not columns.get("scan_execution_inputs", frozenset()) >= _EXECUTION_INPUT_COLUMNS:
        return ()
    return tuple(
        connection.execute(
            """
            SELECT scan.id, scan.completed_at, snapshot.event_id,
                   inputs.monotonic_started, inputs.monotonic_finished,
                   inputs.logs_json, inputs.traces_json, inputs.context_events_json,
                   inputs.canonical_activities_json
            FROM scan_runs AS scan
            JOIN native_pipeline_snapshots AS snapshot ON snapshot.scan_run_id = scan.id
            LEFT JOIN scan_execution_inputs AS inputs ON inputs.scan_run_id = scan.id
            WHERE scan.status IN ('succeeded', 'no_data', 'failed')
              AND scan.completed_at > ? AND scan.completed_at <= ?
            ORDER BY scan.completed_at, scan.id, snapshot.event_id
            """,
            (request.start.isoformat(), request.end.isoformat()),
        ).fetchall()
    )


def _candidate(row: sqlite3.Row) -> CompletedScanSnapshotCandidate:
    logs, traces, contexts, activities = (_population_count(value) for value in row[5:9])
    return CompletedScanSnapshotCandidate(
        scan_id=_digest(str(row[0])),
        source_time=_instant(row[1]),
        payload_schema_version=None,
        terminal_class=None,
        duration_ms=_duration(row),
        error_class=None,
        counts=SnapshotPopulationCounts(
            rows=logs + traces if logs is not None and traces is not None else None,
            logs=logs,
            traces=traces,
            context_events=contexts,
            canonical_activities=activities,
            source_sessions=None,
            pending_outbox=None,
            failed_during_drain=None,
        ),
        bounded_drain_id=None,
    )


def _with_native_observation_metrics(
    proof: PipelineExperimentProof, candidates: tuple[CompletedScanSnapshotCandidate, ...]
) -> PipelineExperimentProof:
    """Expose safe observed scalars while retaining the blocked E-Pipeline proof."""
    selected = candidates[0] if len(candidates) == 1 else None
    metrics = {
        **proof.metrics,
        "observed_native_snapshot_count": len(candidates),
        "observed_native_duration_ms": selected.duration_ms if selected is not None else None,
        "observed_native_rows_processed": selected.counts.rows if selected is not None else None,
    }
    return PipelineExperimentProof(
        proof.experiment_id,
        proof.run_id,
        proof.result,
        proof.provenance,
        proof.source_boundary,
        metrics,
        proof.assertions,
        proof.evidence_ids,
        proof.blocked_boundaries,
        proof.proposal,
    )


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _instant(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        instant = datetime.fromisoformat(value)
    except ValueError:
        return None
    return instant if instant.tzinfo is not None else None


def _population_count(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("native acquisition population must be an immutable JSON array")
    population = json.loads(value)
    if not isinstance(population, list) or any(
        not isinstance(identity, str) or not identity for identity in population
    ):
        raise ValueError("native acquisition population must contain source identities")
    return len(population)


def _duration(row: sqlite3.Row) -> int | float | None:
    started, finished = _number(row[3]), _number(row[4])
    if started is None or finished is None:
        return None
    return _number((finished - started) * 1000)


def _number(value: object) -> int | float | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        return None
    return value

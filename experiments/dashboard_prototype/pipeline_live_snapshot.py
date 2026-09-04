"""Read-only live adapter for bounded pipeline snapshot proofs."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from typing import Final

from experiments.dashboard_prototype.contracts import (
    EvidenceProvenance,
    ExperimentResult,
)
from experiments.dashboard_prototype.pipeline_common import (
    PipelineExperimentId,
    PipelineExperimentProof,
)
from experiments.dashboard_prototype.pipeline_live_common import (
    LiveExperimentEvidence,
    LiveProofRequest,
    RemoteCalculationPrimitive,
)
from experiments.dashboard_prototype.pipeline_snapshot import (
    CompletedScanSnapshotCandidate,
    DurableSnapshotPopulationOracle,
    PipelineSnapshotProofInput,
    ScanErrorClass,
    ScanTerminalClass,
    SnapshotPopulationCounts,
    build_pipeline_snapshot_proof,
)

_EVENT_NAME: Final = "dashboard.pipeline.snapshot"


def extract(connection: sqlite3.Connection, request: LiveProofRequest) -> LiveExperimentEvidence:
    """Extract a bounded immutable snapshot and independently reconcile its counts."""
    columns = _columns(connection)
    scans = _scan_rows(connection, request, columns)
    candidates = tuple(_candidate(row) for row in scans)
    oracle = _oracle(connection, columns, scans, candidates)
    proof = build_pipeline_snapshot_proof(
        run_id=request.run_id,
        provenance=EvidenceProvenance.FRESH_REAL,
        source_boundary=request.source_boundary,
        proof_input=PipelineSnapshotProofInput(candidates, request.start, request.end, oracle),
    )
    authority_missing = _authority_missing_boundaries(scans, candidates)
    if proof.result is ExperimentResult.PROVEN and authority_missing:
        proof = PipelineExperimentProof(
            experiment_id=proof.experiment_id,
            run_id=proof.run_id,
            result=ExperimentResult.BLOCKED,
            provenance=proof.provenance,
            source_boundary=proof.source_boundary,
            metrics=proof.metrics,
            assertions={**proof.assertions, "snapshot_authority_complete": False},
            evidence_ids=proof.evidence_ids,
            blocked_boundaries=authority_missing,
            proposal="Block execution until every snapshot authority field is present.",
        )
    primitives = _primitives(proof, scans, candidates)
    remote_query_id = f"snapshot-{request.run_id}" if primitives else None
    return LiveExperimentEvidence(proof, primitives, remote_query_id)


def _columns(connection: sqlite3.Connection) -> dict[str, frozenset[str]]:
    names = (
        "scan_runs",
        "source_session_records",
        "canonical_activities",
        "session_context_events",
        "otlp_outbox",
        "outbox_drain_attempts",
        "pipeline_drain_attempts",
    )
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
    if not {"id", "status", "completed_at", "rows_processed"} <= columns.get(
        "scan_runs", frozenset()
    ):
        return ()
    bounds = (request.start.isoformat(), request.end.isoformat())
    if "otlp_outbox" not in columns or not {"event_id", "payload_json"} <= columns["otlp_outbox"]:
        return tuple(
            connection.execute(
                """
                SELECT id, status, completed_at, rows_processed, NULL, NULL, NULL, NULL,
                       NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
                FROM scan_runs
                WHERE status IN ('succeeded', 'no_data', 'failed')
                  AND completed_at > ? AND completed_at <= ?
                ORDER BY completed_at, id
                """,
                bounds,
            ).fetchall()
        )
    return tuple(
        connection.execute(
            """
            SELECT scan.id, scan.status, scan.completed_at, scan.rows_processed,
                   json_extract(outbox.payload_json, '$."entity.id"'),
                   json_extract(outbox.payload_json, '$."pipeline.payload_schema_version"'),
                   json_extract(outbox.payload_json, '$."scan.terminal_class"'),
                   json_extract(outbox.payload_json, '$."scan.duration_ms"'),
                   json_extract(outbox.payload_json, '$."scan.error_class"'),
                   json_extract(outbox.payload_json, '$."logs.count"'),
                   json_extract(outbox.payload_json, '$."traces.count"'),
                   json_extract(outbox.payload_json, '$."context_events.count"'),
                   json_extract(outbox.payload_json, '$."canonical_activities.count"'),
                   json_extract(outbox.payload_json, '$."source_sessions.count"'),
                   json_extract(outbox.payload_json, '$."outbox.pending.count"'),
                   json_extract(outbox.payload_json, '$."outbox.failed_during_drain.count"'),
                   json_extract(outbox.payload_json, '$."pipeline.bounded_drain_id"'),
                   outbox.event_id,
                   json_extract(outbox.payload_json, '$."scan.completed_at_ns"')
            FROM scan_runs AS scan
            LEFT JOIN otlp_outbox AS outbox
              ON outbox.event_id IS NOT NULL
             AND json_extract(outbox.payload_json, '$."event.name"') = ?
             AND json_extract(outbox.payload_json, '$."entity.id"') = scan.id
            WHERE scan.status IN ('succeeded', 'no_data', 'failed')
              AND scan.completed_at > ? AND scan.completed_at <= ?
            ORDER BY scan.completed_at, scan.id, outbox.event_id
            """,
            (_EVENT_NAME, *bounds),
        ).fetchall()
    )


def _candidate(row: sqlite3.Row) -> CompletedScanSnapshotCandidate:
    terminal_class = _terminal(row[6])
    error_class = _error(row[8])
    expected = _status_payload_class(row[1])
    if expected is not None and (terminal_class, error_class) != expected:
        terminal_class, error_class = terminal_class, expected[1]
    completed_at = _instant(row[2])
    return CompletedScanSnapshotCandidate(
        scan_id=_digest(str(row[0])),
        source_time=completed_at,
        payload_schema_version=_integer(row[5]),
        terminal_class=terminal_class,
        duration_ms=_integer(row[7]),
        error_class=error_class,
        counts=SnapshotPopulationCounts(
            rows=_integer(row[3]),
            logs=_integer(row[9]),
            traces=_integer(row[10]),
            context_events=_integer(row[11]),
            canonical_activities=_integer(row[12]),
            source_sessions=_integer(row[13]),
            pending_outbox=_integer(row[14]),
            failed_during_drain=_integer(row[15]),
        ),
        bounded_drain_id=_digest(str(row[16])) if isinstance(row[16], str) and row[16] else None,
    )


def _oracle(
    connection: sqlite3.Connection,
    columns: dict[str, frozenset[str]],
    scans: tuple[sqlite3.Row, ...],
    candidates: tuple[CompletedScanSnapshotCandidate, ...],
) -> DurableSnapshotPopulationOracle | None:
    if not scans or any(candidate.source_time is None for candidate in candidates):
        return None
    latest_candidate = max(
        candidates, key=lambda candidate: (candidate.source_time, candidate.scan_id)
    )
    latest = next(row for row in scans if _digest(str(row[0])) == latest_candidate.scan_id)
    drain_id = latest[16]
    if not isinstance(drain_id, str) or not drain_id:
        return None
    rows = _integer(latest[3])
    source_counts = _source_counts(connection, columns, str(latest[0]))
    activities = _scan_count(connection, columns, "canonical_activities", str(latest[0]))
    context_events = _scan_count(connection, columns, "session_context_events", str(latest[0]))
    drain_population = _drain_population(connection, columns, drain_id)
    if drain_population is None:
        return None
    return DurableSnapshotPopulationOracle(
        SnapshotPopulationCounts(
            rows,
            source_counts[0],
            source_counts[1],
            context_events,
            activities,
            source_counts[2],
            drain_population[0],
            drain_population[1],
        ),
        _digest(drain_id),
    )


def _source_counts(
    connection: sqlite3.Connection, columns: dict[str, frozenset[str]], scan_id: str
) -> tuple[int | None, int | None, int | None]:
    required = {"scan_run_id", "source_kind"}
    if not required <= columns.get("source_session_records", frozenset()):
        return None, None, None
    row = connection.execute(
        """SELECT count(*) FILTER (WHERE source_kind = 'log'),
                  count(*) FILTER (WHERE source_kind = 'trace'), count(*)
           FROM source_session_records WHERE scan_run_id = ?""",
        (scan_id,),
    ).fetchone()
    assert row is not None
    return int(row[0]), int(row[1]), int(row[2])


def _scan_count(
    connection: sqlite3.Connection,
    columns: dict[str, frozenset[str]],
    table: str,
    scan_id: str,
) -> int | None:
    """Count a population only when its rows retain exact scan membership."""
    if "scan_run_id" not in columns.get(table, frozenset()):
        return None
    row = connection.execute(
        f"SELECT count(*) FROM {table} WHERE scan_run_id = ?", (scan_id,)
    ).fetchone()
    return int(row[0]) if row else None


def _drain_population(
    connection: sqlite3.Connection, columns: dict[str, frozenset[str]], drain_id: str
) -> tuple[int, int] | None:
    """Read the one immutable population snapshot recorded for a bounded drain."""
    required = {"drain_id", "pending_outbox_count", "failed_during_drain_count"}
    for table in ("outbox_drain_attempts", "pipeline_drain_attempts"):
        if required <= columns.get(table, frozenset()):
            rows = connection.execute(
                f"""SELECT pending_outbox_count, failed_during_drain_count
                    FROM {table} WHERE drain_id = ?""",
                (drain_id,),
            ).fetchall()
            if len(rows) != 1:
                return None
            pending, failed = _integer(rows[0][0]), _integer(rows[0][1])
            return (pending, failed) if pending is not None and failed is not None else None
    return None


def _authority_missing_boundaries(
    scans: tuple[sqlite3.Row, ...],
    candidates: tuple[CompletedScanSnapshotCandidate, ...],
) -> tuple[str, ...]:
    """Return missing fields from every immutable event needed by direct SQL."""
    missing: list[str] = []
    for row, candidate in zip(scans, candidates, strict=True):
        for field, index in (
            ("event_id", 17),
            ("terminal_class", 6),
            ("error_class", 8),
            ("bounded_drain_id", 16),
        ):
            if not isinstance(row[index], str) or not row[index]:
                missing.append(f"snapshot.{field}")
        if row[6] not in {item.value for item in ScanTerminalClass}:
            missing.append("snapshot.terminal_class")
        if row[8] not in {item.value for item in ScanErrorClass}:
            missing.append("snapshot.error_class")
        for field, index in (
            ("completed_at_ns", 18),
            ("payload_schema_version", 5),
            ("duration_ms", 7),
            ("rows", 3),
            ("logs", 9),
            ("traces", 10),
            ("context_events", 11),
            ("canonical_activities", 12),
            ("source_sessions", 13),
            ("pending_outbox", 14),
            ("failed_during_drain", 15),
        ):
            if _integer(row[index]) is None:
                missing.append(f"snapshot.{field}")
        missing.extend(candidate.missing_boundaries())
    return tuple(sorted(set(missing)))


def _primitives(
    proof: PipelineExperimentProof,
    scans: tuple[sqlite3.Row, ...],
    candidates: tuple[CompletedScanSnapshotCandidate, ...],
) -> tuple[RemoteCalculationPrimitive, ...]:
    if proof.result is not ExperimentResult.PROVEN:
        return ()
    records = sorted(
        zip(scans, candidates, strict=True),
        key=lambda pair: (pair[1].source_time, pair[1].scan_id, str(pair[0][17])),
    )
    primitives: list[RemoteCalculationPrimitive] = []
    for ordinal, (row, candidate) in enumerate(records):
        source_time = candidate.source_time
        completed_at_ns = _integer(row[18])
        event_id = row[17]
        drain_id = row[16]
        terminal_class = row[6]
        error_class = row[8]
        assert source_time is not None
        assert candidate.payload_schema_version is not None
        assert candidate.duration_ms is not None
        assert completed_at_ns is not None
        assert isinstance(event_id, str)
        assert event_id
        assert isinstance(drain_id, str)
        assert drain_id
        assert isinstance(terminal_class, str)
        assert terminal_class in {item.value for item in ScanTerminalClass}
        assert isinstance(error_class, str)
        assert error_class in {item.value for item in ScanErrorClass}
        counts = candidate.counts.values()
        primitives.append(
            RemoteCalculationPrimitive(
                PipelineExperimentId.SNAPSHOT,
                source_time,
                ordinal,
                {
                    "event_id": event_id,
                    "scan_digest": candidate.scan_id,
                    "bounded_drain_id": drain_id,
                    "terminal_class": terminal_class,
                    "error_class": error_class,
                },
                {
                    "completed_at_ns": completed_at_ns,
                    "payload_schema_version": candidate.payload_schema_version,
                    "duration_ms": candidate.duration_ms,
                    "rows": counts[0],
                    "logs": counts[1],
                    "traces": counts[2],
                    "context_events": counts[3],
                    "canonical_activities": counts[4],
                    "source_sessions": counts[5],
                    "pending_outbox": counts[6],
                    "failed_during_drain": counts[7],
                },
            )
        )
    return tuple(primitives)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _instant(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return instant if instant.tzinfo is not None else None


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _status_payload_class(
    status: object,
) -> tuple[ScanTerminalClass, ScanErrorClass] | None:
    if not isinstance(status, str):
        return None
    return {
        "succeeded": (ScanTerminalClass.COMPLETED, ScanErrorClass.NONE),
        "no_data": (ScanTerminalClass.COMPLETED, ScanErrorClass.NONE),
        "failed": (ScanTerminalClass.FAILED, ScanErrorClass.SCAN_ERROR),
    }.get(status)


def _terminal(value: object) -> ScanTerminalClass | None:
    if not isinstance(value, str):
        return None
    return {"completed": ScanTerminalClass.COMPLETED, "failed": ScanTerminalClass.FAILED}.get(value)


def _error(value: object) -> ScanErrorClass | None:
    if not isinstance(value, str):
        return None
    return {"none": ScanErrorClass.NONE, "scan-error": ScanErrorClass.SCAN_ERROR}.get(value)

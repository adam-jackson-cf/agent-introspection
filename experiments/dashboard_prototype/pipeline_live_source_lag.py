"""Bounded SQLite extraction for the E-Pipeline source-lag proof."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime

from agent_introspection.source import CANONICAL_SERVICE_PRODUCERS
from experiments.dashboard_prototype.contracts import EvidenceProvenance
from experiments.dashboard_prototype.pipeline_common import PipelineExperimentId
from experiments.dashboard_prototype.pipeline_live_common import (
    LiveExperimentEvidence,
    LiveProofRequest,
    RemoteCalculationPrimitive,
)
from experiments.dashboard_prototype.pipeline_source_lag import (
    AuthoritativeSourceObservation,
    SourceLagCohort,
    SourceLagReduction,
    SourceLagScan,
    SourceLagSelection,
    build_source_lag_proof,
    reduce_source_lag,
)

_SUPPORTED_PRODUCERS = frozenset({"codex-cli", "codex-app-server", "omp"})
_NORMATIVE_SIGNALS = ("logs", "traces", "lifecycle")
_SOURCE_SIGNAL_BY_KIND = {"log": "logs", "trace": "traces"}
_REMOTE_QUERY_ID = "pipeline-source-lag-v1"


def extract(connection: sqlite3.Connection, request: LiveProofRequest) -> LiveExperimentEvidence:
    """Extract bounded source observations without reading native-identity JSON fields."""
    missing = _missing_tables(connection)
    if missing:
        return _blocked_evidence(request, tuple(f"sqlite.schema.{name}" for name in missing))

    scan_rows = tuple(
        connection.execute(
            """
            SELECT id, started_at, completed_at, source_end_ns
            FROM scan_runs
            WHERE status = 'succeeded'
              AND completed_at > ? AND completed_at <= ?
              AND source_end_ns IS NOT NULL
            ORDER BY completed_at, id
            """,
            (request.start.isoformat(), request.end.isoformat()),
        )
    )
    record_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(source_session_records)")
    }
    context_evidence_id = (
        "context_evidence_id" if "context_evidence_id" in record_columns else "NULL"
    )
    record_rows = tuple(
        connection.execute(
            f"""
            SELECT scan_run_id, source_kind, service_name, source_id, source_timestamp,
                   {context_evidence_id}
            FROM source_session_records
            WHERE scan_run_id IN (
                SELECT id FROM scan_runs
                WHERE status = 'succeeded'
                  AND completed_at > ? AND completed_at <= ?
                  AND source_end_ns IS NOT NULL
            )
            ORDER BY scan_run_id, source_kind, service_name, source_timestamp, source_id
            """,
            (request.start.isoformat(), request.end.isoformat()),
        )
    )
    scan_metadata, scan_blocked, invalid_scan = _scan_metadata(scan_rows)
    if invalid_scan:
        return _failed_evidence(request)
    blocked = scan_blocked
    if not scan_metadata:
        return _blocked_evidence(request, tuple(sorted(blocked or {"scan-runs"})))
    normative_cohorts = tuple(
        SourceLagCohort(producer, surface, signal)
        for producer, surface in sorted(
            {
                (producer, surface)
                for producer, surface in CANONICAL_SERVICE_PRODUCERS.values()
                if producer in _SUPPORTED_PRODUCERS
            }
        )
        for signal in _NORMATIVE_SIGNALS
    )
    scans = {
        (scan_id, cohort): SourceLagScan(
            scan_id=scan_id,
            cohort=cohort,
            population_start=request.start,
            population_end=request.end,
            started_at=metadata[0],
            completed_at=metadata[1],
            extraction_bound=metadata[2],
            capability_available=True,
        )
        for scan_id, metadata in scan_metadata.items()
        for cohort in normative_cohorts
    }
    observations, observation_blocked, conflicting_observation = _observations(
        record_rows, scan_metadata
    )
    if conflicting_observation:
        return _failed_evidence(request)
    blocked.update(observation_blocked)
    lifecycle_observations, lifecycle_blocked, conflicting_lifecycle = _lifecycle_observations(
        connection,
        record_rows,
        scan_metadata,
        "context_evidence_id" in record_columns,
    )
    if conflicting_lifecycle:
        return _failed_evidence(request)
    blocked.update(lifecycle_blocked)
    observations.extend(lifecycle_observations)
    reduction = reduce_source_lag(scans.values(), observations, normative_cohorts)
    if blocked:
        reduction = SourceLagReduction(
            reduction.selections,
            reduction.cohorts,
            tuple(sorted(set(reduction.blocked_boundaries) | blocked)),
        )
    proof = build_source_lag_proof(
        request.run_id, EvidenceProvenance.FRESH_REAL, request.source_boundary, reduction
    )
    primitives = tuple(
        RemoteCalculationPrimitive(
            experiment_id=PipelineExperimentId.SOURCE_LAG,
            source_time=scan.completed_at,
            ordinal=ordinal,
            dimensions={
                "cohort": _hash(("cohort", selection.cohort.boundary)),
                "disposition": selection.disposition.value,
                "scan_ordinal": ordinal,
            },
            measures={
                "lag_seconds": _lag_seconds(selection)
                if selection.disposition.value == "accepted"
                else 0.0,
                "negative_skew": int(
                    selection.disposition.value == "accepted" and selection.negative_skew
                ),
            },
        )
        for ordinal, (selection, scan) in enumerate(_selected_scans(reduction, scans), start=1)
    )
    oracle = {
        _hash(("cohort", metric.cohort.boundary)): {
            "population": metric.population,
            "accepted": metric.accepted,
            "missing_capability": metric.missing_capability,
            "rejected": metric.rejected,
            "duplicate": metric.duplicate,
            "n": metric.n,
            "p50_lag_seconds": metric.p50_lag_seconds or 0.0,
            "p95_lag_seconds": metric.p95_lag_seconds or 0.0,
            "negative_skew_count": metric.negative_skew_count,
        }
        for metric in reduction.cohorts
    }
    return LiveExperimentEvidence(
        proof, primitives, _REMOTE_QUERY_ID if primitives else None, oracle
    )


def _scan_metadata(
    rows: tuple[tuple[object, ...], ...],
) -> tuple[dict[str, tuple[datetime, datetime, datetime]], set[str], bool]:
    blocked: set[str] = set()
    metadata: dict[str, tuple[datetime, datetime, datetime]] = {}
    for row in rows:
        try:
            started = _instant(row[1])
            completed = _instant(row[2])
            extraction_bound = datetime.fromtimestamp(int(str(row[3])) / 1_000_000_000, UTC)
        except (TypeError, ValueError, OverflowError, OSError):
            blocked.add(f"source-lag.timestamp:{_hash(('scan', row[0]))}")
            continue
        if extraction_bound > completed:
            return metadata, blocked, True
        metadata[_hash(("scan", row[0]))] = (started, completed, extraction_bound)
    return metadata, blocked, False


def _observations(
    rows: tuple[tuple[object, ...], ...],
    scan_metadata: dict[str, tuple[datetime, datetime, datetime]],
) -> tuple[list[AuthoritativeSourceObservation], set[str], bool]:
    blocked: set[str] = set()
    observations: list[AuthoritativeSourceObservation] = []
    observed_times: dict[tuple[str, SourceLagCohort, str], datetime] = {}
    for row in rows:
        service = str(row[2])
        mapped = CANONICAL_SERVICE_PRODUCERS.get(service)
        signal = _SOURCE_SIGNAL_BY_KIND.get(str(row[1]))
        if mapped is None or mapped[0] not in _SUPPORTED_PRODUCERS or signal is None:
            continue
        try:
            source_time = _instant(row[4])
        except (TypeError, ValueError, OverflowError, OSError):
            blocked.add(f"source-lag.timestamp:{_hash((row[0], row[1], service))}")
            continue
        scan_id = _hash(("scan", row[0]))
        metadata = scan_metadata.get(scan_id)
        if metadata is None:
            blocked.add(f"source-lag.scan-metadata:{scan_id}")
            continue
        cohort = SourceLagCohort(mapped[0], mapped[1], signal)
        source_identity = _hash(("source", row[1], service, row[3]))
        source_key = (scan_id, cohort, source_identity)
        if observed_times.setdefault(source_key, source_time) != source_time:
            return observations, blocked, True
        observations.append(
            AuthoritativeSourceObservation(
                observation_id=source_identity,
                scan_id=scan_id,
                cohort=cohort,
                source_time=source_time,
                current_identity=_hash(("current", row[1], service, row[3])),
                extraction_bound=metadata[2],
            )
        )
    return observations, blocked, False


def _lifecycle_observations(
    connection: sqlite3.Connection,
    record_rows: tuple[tuple[object, ...], ...],
    scan_metadata: dict[str, tuple[datetime, datetime, datetime]],
    has_context_evidence_id: bool,
) -> tuple[list[AuthoritativeSourceObservation], set[str], bool]:
    columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(session_context_events)")
    }
    if not has_context_evidence_id or not {"event_id", "producer", "occurred_at"} <= columns:
        return [], {"lifecycle-authority"}, False
    referenced_contexts = {
        (_hash(("scan", row[0])), str(row[5]))
        for row in record_rows
        if row[5] is not None and str(row[5])
    }
    rows = tuple(
        connection.execute(
            """
            SELECT event_id, producer, occurred_at
            FROM session_context_events
            WHERE producer IN ('codex-cli', 'codex-app-server', 'omp')
            ORDER BY producer, occurred_at, event_id
            """
        )
    )
    blocked: set[str] = set()
    observations: list[AuthoritativeSourceObservation] = []
    observed_times: dict[tuple[str, SourceLagCohort, str], datetime] = {}
    for event_id, producer, occurred_at in rows:
        mapped = CANONICAL_SERVICE_PRODUCERS.get(str(producer))
        if mapped is None or mapped[0] not in _SUPPORTED_PRODUCERS:
            continue
        try:
            source_time = _instant(occurred_at)
        except (TypeError, ValueError, OverflowError, OSError):
            blocked.add(f"source-lag.timestamp:{_hash(('lifecycle', producer, event_id))}")
            continue
        cohort = SourceLagCohort(mapped[0], mapped[1], "lifecycle")
        source_identity = _hash(("lifecycle", producer, event_id))
        for scan_id, context_evidence_id in referenced_contexts:
            if context_evidence_id != str(event_id):
                continue
            metadata = scan_metadata.get(scan_id)
            if metadata is None:
                blocked.add(f"source-lag.scan-metadata:{scan_id}")
                continue
            source_key = (scan_id, cohort, source_identity)
            if observed_times.setdefault(source_key, source_time) != source_time:
                return observations, blocked, True
            observations.append(
                AuthoritativeSourceObservation(
                    observation_id=source_identity,
                    scan_id=scan_id,
                    cohort=cohort,
                    source_time=source_time,
                    current_identity=_hash(("lifecycle-current", producer, event_id)),
                    extraction_bound=metadata[2],
                )
            )
    return observations, blocked, False


def _selected_scans(
    reduction: SourceLagReduction,
    scans: dict[tuple[str, SourceLagCohort], SourceLagScan],
) -> tuple[tuple[SourceLagSelection, SourceLagScan], ...]:
    return tuple(
        (selection, scans[(selection.scan_id, selection.cohort)])
        for selection in reduction.selections
        if (selection.scan_id, selection.cohort) in scans
    )


def _lag_seconds(selection: SourceLagSelection) -> float:
    if selection.lag_seconds is None:
        raise ValueError("source-lag primitive requires an observed lag")
    return selection.lag_seconds


def _failed_evidence(request: LiveProofRequest) -> LiveExperimentEvidence:
    proof = build_source_lag_proof(
        request.run_id,
        EvidenceProvenance.FRESH_REAL,
        request.source_boundary,
        SourceLagReduction((), (), ()),
    )
    return LiveExperimentEvidence(proof, (), None)


def _blocked_evidence(
    request: LiveProofRequest, boundaries: tuple[str, ...]
) -> LiveExperimentEvidence:
    reduction = SourceLagReduction((), (), boundaries)
    proof = build_source_lag_proof(
        request.run_id, EvidenceProvenance.FRESH_REAL, request.source_boundary, reduction
    )
    return LiveExperimentEvidence(proof, (), None)


def _missing_tables(connection: sqlite3.Connection) -> tuple[str, ...]:
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    return tuple(sorted({"scan_runs", "source_session_records"} - tables))


def _instant(value: object) -> datetime:
    instant = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return instant


def _hash(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()

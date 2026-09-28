"""Pure E-Pipeline-1 reducer for bounded completed scan snapshots."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from experiments.dashboard_prototype.contracts import EvidenceProvenance, ExperimentResult
from experiments.dashboard_prototype.pipeline_common import (
    PipelineExperimentId,
    PipelineExperimentProof,
)

SNAPSHOT_PAYLOAD_SCHEMA_VERSION = 1
SnapshotPopulationCountValues = tuple[int, int, int, int, int, int, int, int]


def _require_nonnegative_integer(value: int | None) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("snapshot population counts must be nonnegative integers")
    return value


class ScanTerminalClass(StrEnum):
    """Terminal scan outcomes retained by the bounded snapshot payload."""

    COMPLETED = "completed"
    FAILED = "failed"


class ScanErrorClass(StrEnum):
    """Allowlisted error classifications for terminal scan payloads."""

    NONE = "none"
    SCAN_ERROR = "scan-error"


@dataclass(frozen=True, slots=True)
class SnapshotPopulationCounts:
    """Allowlisted count-only view of one durable population."""

    rows: int | None
    logs: int | None
    traces: int | None
    context_events: int | None
    canonical_activities: int | None
    source_sessions: int | None
    pending_outbox: int | None
    failed_during_drain: int | None

    def missing_boundaries(self, prefix: str) -> tuple[str, ...]:
        return tuple(
            f"{prefix}.{name}"
            for name, value in (
                ("rows", self.rows),
                ("logs", self.logs),
                ("traces", self.traces),
                ("context_events", self.context_events),
                ("canonical_activities", self.canonical_activities),
                ("source_sessions", self.source_sessions),
                ("pending_outbox", self.pending_outbox),
                ("failed_during_drain", self.failed_during_drain),
            )
            if value is None
        )

    def values(self) -> SnapshotPopulationCountValues:
        return (
            _require_nonnegative_integer(self.rows),
            _require_nonnegative_integer(self.logs),
            _require_nonnegative_integer(self.traces),
            _require_nonnegative_integer(self.context_events),
            _require_nonnegative_integer(self.canonical_activities),
            _require_nonnegative_integer(self.source_sessions),
            _require_nonnegative_integer(self.pending_outbox),
            _require_nonnegative_integer(self.failed_during_drain),
        )

    def workload_count(self) -> int:
        """Return the canonical row workload without mixing population domains."""
        return _require_nonnegative_integer(self.rows)


@dataclass(frozen=True, slots=True)
class CompletedScanSnapshotCandidate:
    """One completed scan payload, already constrained to allowlisted fields."""

    scan_id: str
    source_time: datetime | None
    payload_schema_version: int | None
    terminal_class: ScanTerminalClass | None
    duration_ms: int | float | None
    error_class: ScanErrorClass | None
    counts: SnapshotPopulationCounts
    bounded_drain_id: str | None

    def missing_boundaries(self) -> tuple[str, ...]:
        missing = list(self.counts.missing_boundaries("snapshot"))
        for name, value in (
            ("scan_id", self.scan_id),
            ("source_time", self.source_time),
            ("payload_schema_version", self.payload_schema_version),
            ("terminal_class", self.terminal_class),
            ("duration_ms", self.duration_ms),
            ("error_class", self.error_class),
            ("bounded_drain_id", self.bounded_drain_id),
        ):
            if value is None or value == "":
                missing.append(f"snapshot.{name}")
        return tuple(missing)

    def validate(self) -> None:
        if self.missing_boundaries():
            raise ValueError("snapshot candidate has missing required fields")
        if self.payload_schema_version != SNAPSHOT_PAYLOAD_SCHEMA_VERSION:
            raise ValueError("snapshot payload schema version is unsupported")
        duration_ms = self.duration_ms
        if (
            duration_ms is None
            or isinstance(duration_ms, bool)
            or not isinstance(duration_ms, (int, float))
            or not math.isfinite(duration_ms)
            or duration_ms < 0
        ):
            raise ValueError("snapshot duration must be a finite nonnegative number")
        if self.terminal_class is ScanTerminalClass.COMPLETED:
            if self.error_class is not ScanErrorClass.NONE:
                raise ValueError("completed snapshot must have no error class")
        elif self.terminal_class is ScanTerminalClass.FAILED:
            if self.error_class is ScanErrorClass.NONE:
                raise ValueError("failed snapshot must have an error class")
        else:
            raise ValueError("snapshot terminal class is unsupported")
        self.counts.values()


@dataclass(frozen=True, slots=True)
class DurableSnapshotPopulationOracle:
    """Independent durable count oracle, including immutable drain-attempt events."""

    counts: SnapshotPopulationCounts
    bounded_drain_id: str | None

    def missing_boundaries(self) -> tuple[str, ...]:
        missing = list(self.counts.missing_boundaries("durable_oracle"))
        if not self.bounded_drain_id:
            missing.append("durable_oracle.bounded_drain_id")
        return tuple(missing)

    def validate(self) -> None:
        if self.missing_boundaries():
            raise ValueError("durable snapshot oracle has missing required fields")
        self.counts.values()


@dataclass(frozen=True, slots=True)
class PipelineSnapshotReduction:
    """Deterministic output of reconciling range-selected scan snapshots."""

    latest_snapshot: CompletedScanSnapshotCandidate
    selected_snapshot_count: int
    latest_workload_count: int
    latest_duration_ms: int | float
    reconciliation_matches: bool


def reduce_pipeline_snapshot(
    snapshots: Sequence[CompletedScanSnapshotCandidate],
    *,
    start: datetime,
    end: datetime,
    durable_oracle: DurableSnapshotPopulationOracle,
) -> PipelineSnapshotReduction:
    """Select ``start < source_time <= end`` and reconcile the latest snapshot."""
    if start >= end:
        raise ValueError("snapshot range requires start before end")
    durable_oracle.validate()
    selected_by_scan_id: dict[str, CompletedScanSnapshotCandidate] = {}
    for snapshot in snapshots:
        snapshot.validate()
        source_time = snapshot.source_time
        assert source_time is not None
        if not start < source_time <= end:
            continue
        existing = selected_by_scan_id.get(snapshot.scan_id)
        if existing is None:
            selected_by_scan_id[snapshot.scan_id] = snapshot
        elif existing != snapshot:
            raise ValueError("conflicting snapshot candidates share a scan identity")
    if not selected_by_scan_id:
        raise ValueError("snapshot range contains no completed scan")
    selected = tuple(selected_by_scan_id.values())
    latest = max(selected, key=lambda snapshot: (snapshot.source_time, snapshot.scan_id))
    latest_duration_ms = latest.duration_ms
    assert latest_duration_ms is not None
    if latest.bounded_drain_id != durable_oracle.bounded_drain_id:
        reconciliation_matches = False
    else:
        reconciliation_matches = latest.counts.values() == durable_oracle.counts.values()
    return PipelineSnapshotReduction(
        latest_snapshot=latest,
        selected_snapshot_count=len(selected),
        latest_workload_count=latest.counts.workload_count(),
        latest_duration_ms=latest_duration_ms,
        reconciliation_matches=reconciliation_matches,
    )


@dataclass(frozen=True, slots=True)
class PipelineSnapshotProofInput:
    """Bounded input populations and source-time range for one proof attempt."""

    snapshots: Sequence[CompletedScanSnapshotCandidate] | None
    start: datetime | None
    end: datetime | None
    durable_oracle: DurableSnapshotPopulationOracle | None

    def missing_boundaries(self) -> tuple[str, ...]:
        blocked: list[str] = []
        if not self.snapshots:
            blocked.append("snapshot_population")
        if self.start is None:
            blocked.append("source_range.start")
        if self.end is None:
            blocked.append("source_range.end")
        if self.durable_oracle is None:
            blocked.append("durable_population_oracle")
        for snapshot in self.snapshots or ():
            blocked.extend(snapshot.missing_boundaries())
        if self.durable_oracle:
            blocked.extend(self.durable_oracle.missing_boundaries())
        return tuple(sorted(set(blocked)))


def build_pipeline_snapshot_proof(
    *,
    run_id: str,
    provenance: EvidenceProvenance,
    source_boundary: str,
    proof_input: PipelineSnapshotProofInput,
) -> PipelineExperimentProof:
    """Build fail-closed proof from independently supplied bounded durable counts."""
    blocked = list(proof_input.missing_boundaries())
    if provenance is not EvidenceProvenance.FRESH_REAL:
        blocked.append("fresh_real_provenance")
    blocked = sorted(set(blocked))
    assertions = {
        "fresh_real_provenance": provenance is EvidenceProvenance.FRESH_REAL,
        "required_boundaries_present": not blocked,
    }
    metrics: dict[str, str | int | float | bool | None] = {
        "selected_snapshot_count": len(proof_input.snapshots or ()),
    }
    if blocked:
        return PipelineExperimentProof(
            PipelineExperimentId.SNAPSHOT,
            run_id,
            ExperimentResult.BLOCKED,
            provenance,
            source_boundary,
            metrics,
            assertions,
            (),
            tuple(blocked),
            "Block cutover until the bounded scan snapshot and durable oracle are complete.",
        )
    snapshots = proof_input.snapshots
    start = proof_input.start
    end = proof_input.end
    durable_oracle = proof_input.durable_oracle
    assert snapshots is not None
    assert start is not None
    assert end is not None
    assert durable_oracle is not None
    try:
        reduction = reduce_pipeline_snapshot(
            snapshots,
            start=start,
            end=end,
            durable_oracle=durable_oracle,
        )
    except ValueError as error:
        return PipelineExperimentProof(
            PipelineExperimentId.SNAPSHOT,
            run_id,
            ExperimentResult.FAILED,
            provenance,
            source_boundary,
            metrics,
            {**assertions, "snapshot_contract_valid": False},
            (),
            (),
            f"Do not cut over: {error}.",
        )
    latest = reduction.latest_snapshot
    latest_source_time = latest.source_time
    assert latest_source_time is not None
    assertions.update(
        {
            "snapshot_contract_valid": True,
            "durable_population_reconciles": reduction.reconciliation_matches,
            "selected_range_operator": start < latest_source_time <= end,
            "terminal_error_consistent": True,
        }
    )
    metrics.update(
        {
            "selected_snapshot_count": reduction.selected_snapshot_count,
            "latest_source_time": latest_source_time.isoformat(),
            "latest_payload_schema_version": latest.payload_schema_version,
            "latest_terminal_class": latest.terminal_class.value if latest.terminal_class else None,
            "latest_error_class": latest.error_class.value if latest.error_class else None,
            "latest_duration_ms": reduction.latest_duration_ms,
            "latest_workload_count": reduction.latest_workload_count,
            "latest_rows": latest.counts.rows,
            "latest_logs": latest.counts.logs,
            "latest_traces": latest.counts.traces,
            "latest_context_events": latest.counts.context_events,
            "latest_canonical_activities": latest.counts.canonical_activities,
            "latest_source_sessions": latest.counts.source_sessions,
            "latest_pending_outbox": latest.counts.pending_outbox,
            "latest_failed_during_drain_attempt_events": latest.counts.failed_during_drain,
            "latest_bounded_drain_id": latest.bounded_drain_id,
        }
    )
    evidence_ids = (f"scan:{latest.scan_id}", f"drain:{latest.bounded_drain_id}")
    result = ExperimentResult.PROVEN if all(assertions.values()) else ExperimentResult.FAILED
    return PipelineExperimentProof(
        PipelineExperimentId.SNAPSHOT,
        run_id,
        result,
        provenance,
        source_boundary,
        metrics,
        assertions,
        evidence_ids,
        (),
        "Reconcile each bounded durable population before dashboard cutover.",
    )


_SNAPSHOT_REMOTE_COLUMNS = (
    "event_id",
    "completed_at_ns",
    "payload_schema_version",
    "terminal_class",
    "duration_ms",
    "error_class",
    "rows",
    "logs",
    "traces",
    "context_events",
    "canonical_activities",
    "source_sessions",
    "pending_outbox",
    "failed_during_drain",
    "bounded_drain_id",
)

# Each statement is intentionally row-specific rather than a reusable query
# framework.  The event ID predicate prevents an unbounded dashboard aggregate
# from being treated as authority for a bounded scan snapshot.
A01_LATEST_PIPELINE_SNAPSHOT_SQL = """
SELECT attributes_string['dashboard.event_id'] AS event_id,
       toUInt64OrNull(attributes_string['dashboard.completed_at_ns']) AS completed_at_ns,
       if(
           mapContains(attributes_number, 'dashboard.payload_schema_version'),
           toUInt32(attributes_number['dashboard.payload_schema_version']),
           NULL
       ) AS payload_schema_version,
       attributes_string['dashboard.terminal_class'] AS terminal_class,
       if(
           mapContains(attributes_number, 'dashboard.duration_ms'),
           toUInt64(attributes_number['dashboard.duration_ms']),
           NULL
       ) AS duration_ms,
       attributes_string['dashboard.error_class'] AS error_class,
       if(mapContains(attributes_number, 'dashboard.rows'),
          toUInt64(attributes_number['dashboard.rows']), NULL) AS rows,
       if(mapContains(attributes_number, 'dashboard.logs'),
          toUInt64(attributes_number['dashboard.logs']), NULL) AS logs,
       if(mapContains(attributes_number, 'dashboard.traces'),
          toUInt64(attributes_number['dashboard.traces']), NULL) AS traces,
       if(mapContains(attributes_number, 'dashboard.context_events'),
          toUInt64(attributes_number['dashboard.context_events']), NULL) AS context_events,
       if(mapContains(attributes_number, 'dashboard.canonical_activities'),
          toUInt64(attributes_number['dashboard.canonical_activities']),
          NULL) AS canonical_activities,
       if(mapContains(attributes_number, 'dashboard.source_sessions'),
          toUInt64(attributes_number['dashboard.source_sessions']), NULL) AS source_sessions,
       if(mapContains(attributes_number, 'dashboard.pending_outbox'),
          toUInt64(attributes_number['dashboard.pending_outbox']), NULL) AS pending_outbox,
       if(mapContains(attributes_number, 'dashboard.failed_during_drain'),
          toUInt64(attributes_number['dashboard.failed_during_drain']),
          NULL) AS failed_during_drain,
       attributes_string['dashboard.bounded_drain_id'] AS bounded_drain_id
FROM signoz_logs.distributed_logs_v2
WHERE attributes_string['event.name'] = 'dashboard_prototype.pipeline_snapshot.v1'
  AND attributes_string['dashboard.event_kind'] = 'primitive'
  AND attributes_string['dashboard.experiment_id'] = 'E-Pipeline-1'
  AND attributes_string['event.id'] IN ({event_ids})
  AND timestamp > {start:DateTime64(9, 'UTC')}
  AND timestamp <= {end:DateTime64(9, 'UTC')}
ORDER BY completed_at_ns DESC, event_id DESC
LIMIT 1
""".strip()

A02_SCAN_OUTCOMES_SQL = """
SELECT attributes_string['dashboard.terminal_class'] AS terminal_class,
       toUInt64(uniqExact(attributes_string['event.id'])) AS terminal_scan_count,
       toFloat64(uniqExact(attributes_string['event.id'])) /
           toFloat64(sum(uniqExact(attributes_string['event.id'])) OVER ()) * 100.0
           AS terminal_scan_percent
FROM signoz_logs.distributed_logs_v2
WHERE attributes_string['event.name'] = 'dashboard_prototype.pipeline_snapshot.v1'
  AND attributes_string['dashboard.event_kind'] = 'primitive'
  AND attributes_string['dashboard.experiment_id'] = 'E-Pipeline-1'
  AND attributes_string['event.id'] IN ({event_ids})
  AND timestamp > {start:DateTime64(9, 'UTC')}
  AND timestamp <= {end:DateTime64(9, 'UTC')}
GROUP BY terminal_class
ORDER BY terminal_class
""".strip()

A05_SCAN_WORKLOAD_DURATION_SQL = """
WITH snapshots AS (
    SELECT DISTINCT attributes_string['event.id'] AS event_id,
           toUInt64(attributes_number['dashboard.duration_ms']) AS duration_ms,
           toUInt64(attributes_number['dashboard.rows']) AS rows
    FROM signoz_logs.distributed_logs_v2
    WHERE attributes_string['event.name'] = 'dashboard_prototype.pipeline_snapshot.v1'
      AND attributes_string['dashboard.event_kind'] = 'primitive'
      AND attributes_string['dashboard.experiment_id'] = 'E-Pipeline-1'
      AND attributes_string['event.id'] IN ({event_ids})
      AND timestamp > {start:DateTime64(9, 'UTC')}
      AND timestamp <= {end:DateTime64(9, 'UTC')}
      AND mapContains(attributes_number, 'dashboard.duration_ms')
      AND mapContains(attributes_number, 'dashboard.rows')
      AND attributes_string['dashboard.terminal_class'] = 'completed'
      AND attributes_string['dashboard.error_class'] = 'none'
)
SELECT toFloat64(quantileExact(0.5)(duration_ms)) AS duration_p50_ms,
       toFloat64(quantileExact(0.95)(duration_ms)) AS duration_p95_ms,
       toFloat64(quantileExact(0.5)(rows)) AS rows_p50,
       toFloat64(quantileExact(0.95)(rows)) AS rows_p95,
       toFloat64(quantileExactIf(0.5)(rows / (duration_ms / 1000.0), duration_ms > 0))
           AS rows_per_second_p50,
       toFloat64(quantileExactIf(0.95)(rows / (duration_ms / 1000.0), duration_ms > 0))
           AS rows_per_second_p95,
       toUInt64(count()) AS successful_scan_count,
       toUInt64(countIf(duration_ms > 0)) AS positive_duration_scan_count
FROM snapshots
""".strip()


@dataclass(frozen=True, slots=True)
class PipelineSnapshotAuthorityRecord:
    """One complete immutable snapshot event used by the three owned rows."""

    event_id: str | None
    completed_at_ns: int | None
    payload_schema_version: int | None
    terminal_class: ScanTerminalClass | None
    duration_ms: int | None
    error_class: ScanErrorClass | None
    counts: SnapshotPopulationCounts
    bounded_drain_id: str | None

    def missing_boundaries(self, prefix: str = "snapshot") -> tuple[str, ...]:
        missing = list(self.counts.missing_boundaries(prefix))
        for name, value in (
            ("event_id", self.event_id),
            ("completed_at_ns", self.completed_at_ns),
            ("payload_schema_version", self.payload_schema_version),
            ("terminal_class", self.terminal_class),
            ("duration_ms", self.duration_ms),
            ("error_class", self.error_class),
            ("bounded_drain_id", self.bounded_drain_id),
        ):
            if value is None or value == "":
                missing.append(f"{prefix}.{name}")
        return tuple(missing)

    def validate(self) -> None:
        if self.missing_boundaries():
            raise ValueError("snapshot authority record has missing required fields")
        if self.payload_schema_version != SNAPSHOT_PAYLOAD_SCHEMA_VERSION:
            raise ValueError("snapshot payload schema version is unsupported")
        if not isinstance(self.completed_at_ns, int) or isinstance(self.completed_at_ns, bool):
            raise ValueError("snapshot completion timestamp must be an integer nanosecond")
        if (
            not isinstance(self.duration_ms, int)
            or isinstance(self.duration_ms, bool)
            or self.duration_ms < 0
        ):
            raise ValueError("snapshot duration must be a nonnegative integer")
        if self.terminal_class is ScanTerminalClass.COMPLETED:
            if self.error_class is not ScanErrorClass.NONE:
                raise ValueError("completed snapshot must have no error class")
        elif self.terminal_class is ScanTerminalClass.FAILED:
            if self.error_class is ScanErrorClass.NONE:
                raise ValueError("failed snapshot must have an error class")
        else:
            raise ValueError("snapshot terminal class is unsupported")
        self.counts.values()


def parse_a01_latest_pipeline_snapshot_row(
    row: Mapping[str, object],
) -> PipelineSnapshotAuthorityRecord:
    """Parse the exact raw result shape returned by the owned snapshot SQL."""
    if set(row) != set(_SNAPSHOT_REMOTE_COLUMNS):
        raise ValueError("snapshot remote row must have the exact declared columns")
    terminal = row["terminal_class"]
    error = row["error_class"]
    return PipelineSnapshotAuthorityRecord(
        event_id=_optional_text(row["event_id"]),
        completed_at_ns=_optional_integer(row["completed_at_ns"]),
        payload_schema_version=_optional_integer(row["payload_schema_version"]),
        terminal_class=(
            ScanTerminalClass(terminal)
            if isinstance(terminal, str) and terminal in {item.value for item in ScanTerminalClass}
            else None
        ),
        duration_ms=_optional_integer(row["duration_ms"]),
        error_class=(
            ScanErrorClass(error)
            if isinstance(error, str) and error in {item.value for item in ScanErrorClass}
            else None
        ),
        counts=SnapshotPopulationCounts(
            rows=_optional_integer(row["rows"]),
            logs=_optional_integer(row["logs"]),
            traces=_optional_integer(row["traces"]),
            context_events=_optional_integer(row["context_events"]),
            canonical_activities=_optional_integer(row["canonical_activities"]),
            source_sessions=_optional_integer(row["source_sessions"]),
            pending_outbox=_optional_integer(row["pending_outbox"]),
            failed_during_drain=_optional_integer(row["failed_during_drain"]),
        ),
        bounded_drain_id=_optional_text(row["bounded_drain_id"]),
    )


@dataclass(frozen=True, slots=True)
class A01LatestPipelineSnapshot:
    event_id: str
    completed_at_ns: int
    payload_schema_version: int
    terminal_class: ScanTerminalClass
    duration_ms: int
    error_class: ScanErrorClass
    counts: SnapshotPopulationCounts
    bounded_drain_id: str
    rows_per_second: float | None


@dataclass(frozen=True, slots=True)
class A01LatestPipelineSnapshotOracle:
    calculation: A01LatestPipelineSnapshot | None
    blocked_boundaries: tuple[str, ...]


def oracle_a01_latest_pipeline_snapshot(
    records: Sequence[PipelineSnapshotAuthorityRecord] | None,
) -> A01LatestPipelineSnapshotOracle:
    """Independently select the latest complete snapshot by `(completed_at_ns, event_id)`."""
    complete, blocked = _complete_snapshot_authority_records(records)
    if blocked:
        return A01LatestPipelineSnapshotOracle(None, blocked)
    latest = max(complete, key=lambda record: (record.completed_at_ns, record.event_id))
    assert latest.event_id is not None
    assert latest.completed_at_ns is not None
    assert latest.payload_schema_version is not None
    assert latest.terminal_class is not None
    assert latest.duration_ms is not None
    assert latest.error_class is not None
    assert latest.bounded_drain_id is not None
    return A01LatestPipelineSnapshotOracle(
        A01LatestPipelineSnapshot(
            latest.event_id,
            latest.completed_at_ns,
            latest.payload_schema_version,
            latest.terminal_class,
            latest.duration_ms,
            latest.error_class,
            latest.counts,
            latest.bounded_drain_id,
            latest.counts.workload_count() / (latest.duration_ms / 1000.0)
            if latest.duration_ms > 0
            else None,
        ),
        (),
    )


@dataclass(frozen=True, slots=True)
class A02ScanOutcome:
    terminal_class: ScanTerminalClass
    terminal_scan_count: int
    terminal_scan_percent: float


@dataclass(frozen=True, slots=True)
class A02ScanOutcomesOracle:
    outcomes: tuple[A02ScanOutcome, ...]
    terminal_scan_count: int | None
    blocked_boundaries: tuple[str, ...]


def parse_a02_scan_outcome_row(row: Mapping[str, object]) -> A02ScanOutcome:
    """Parse one typed terminal-class aggregate returned by A02 SQL."""
    if set(row) != {
        "terminal_class",
        "terminal_scan_count",
        "terminal_scan_percent",
    }:
        raise ValueError("A02 remote row must have the exact declared columns")
    terminal_class = row["terminal_class"]
    count = _optional_integer(row["terminal_scan_count"])
    percent = row["terminal_scan_percent"]
    if (
        not isinstance(terminal_class, str)
        or terminal_class not in ScanTerminalClass
        or count is None
        or not isinstance(percent, float)
        or not math.isfinite(percent)
    ):
        raise ValueError("A02 remote row has invalid scalar types")
    return A02ScanOutcome(ScanTerminalClass(terminal_class), count, percent)


def oracle_a02_scan_outcomes(
    records: Sequence[PipelineSnapshotAuthorityRecord] | None,
) -> A02ScanOutcomesOracle:
    """Independently calculate each terminal-class share of exact scan events."""
    complete, blocked = _complete_snapshot_authority_records(records)
    if blocked:
        return A02ScanOutcomesOracle((), None, blocked)
    counts = {
        terminal_class: sum(record.terminal_class is terminal_class for record in complete)
        for terminal_class in ScanTerminalClass
    }
    terminal_scan_count = len(complete)
    return A02ScanOutcomesOracle(
        tuple(
            A02ScanOutcome(
                terminal_class,
                count,
                100.0 * count / terminal_scan_count,
            )
            for terminal_class, count in counts.items()
            if count
        ),
        terminal_scan_count,
        (),
    )


@dataclass(frozen=True, slots=True)
class A05ScanWorkloadDuration:
    successful_scan_count: int
    positive_duration_scan_count: int
    duration_p50_ms: float | None
    duration_p95_ms: float | None
    rows_p50: float | None
    rows_p95: float | None
    rows_per_second_p50: float | None
    rows_per_second_p95: float | None


@dataclass(frozen=True, slots=True)
class A05ScanWorkloadDurationOracle:
    calculation: A05ScanWorkloadDuration | None
    blocked_boundaries: tuple[str, ...]


def parse_a05_scan_workload_duration_row(
    row: Mapping[str, object],
) -> A05ScanWorkloadDuration:
    """Parse the one typed percentile aggregate returned by A05 SQL."""
    fields = {
        "successful_scan_count",
        "positive_duration_scan_count",
        "duration_p50_ms",
        "duration_p95_ms",
        "rows_p50",
        "rows_p95",
        "rows_per_second_p50",
        "rows_per_second_p95",
    }
    if set(row) != fields:
        raise ValueError("A05 remote row must have the exact declared columns")
    successful = _optional_integer(row["successful_scan_count"])
    positive_duration = _optional_integer(row["positive_duration_scan_count"])
    scalar_names = tuple(sorted(fields - {"successful_scan_count", "positive_duration_scan_count"}))
    scalars = {name: _optional_float(row[name]) for name in scalar_names}
    if successful is None or positive_duration is None or positive_duration > successful:
        raise ValueError("A05 remote row has invalid counts")
    if any(row[name] is not None and scalars[name] is None for name in scalar_names):
        raise ValueError("A05 remote percentile fields must be floats or null")
    if successful == 0 and any(value is not None for value in scalars.values()):
        raise ValueError("empty A05 population must not invent percentiles")
    return A05ScanWorkloadDuration(
        successful,
        positive_duration,
        scalars["duration_p50_ms"],
        scalars["duration_p95_ms"],
        scalars["rows_p50"],
        scalars["rows_p95"],
        scalars["rows_per_second_p50"],
        scalars["rows_per_second_p95"],
    )


def oracle_a05_scan_workload_duration(
    records: Sequence[PipelineSnapshotAuthorityRecord] | None,
) -> A05ScanWorkloadDurationOracle:
    """Independently calculate successful scan workload and duration percentiles."""
    complete, blocked = _complete_snapshot_authority_records(records)
    if blocked:
        return A05ScanWorkloadDurationOracle(None, blocked)
    successful = tuple(
        record
        for record in complete
        if record.terminal_class is ScanTerminalClass.COMPLETED
        and record.error_class is ScanErrorClass.NONE
    )
    durations = [duration for record in successful if (duration := record.duration_ms) is not None]
    rows = [record.counts.workload_count() for record in successful]
    throughputs = [
        record.counts.workload_count() / (duration / 1000.0)
        for record in successful
        if (duration := record.duration_ms) is not None and duration > 0
    ]
    return A05ScanWorkloadDurationOracle(
        A05ScanWorkloadDuration(
            len(successful),
            len(throughputs),
            _nearest_rank_percentile(durations, 0.5),
            _nearest_rank_percentile(durations, 0.95),
            _nearest_rank_percentile(rows, 0.5),
            _nearest_rank_percentile(rows, 0.95),
            _nearest_rank_percentile(throughputs, 0.5),
            _nearest_rank_percentile(throughputs, 0.95),
        ),
        (),
    )


def _complete_snapshot_authority_records(
    records: Sequence[PipelineSnapshotAuthorityRecord] | None,
) -> tuple[tuple[PipelineSnapshotAuthorityRecord, ...], tuple[str, ...]]:
    if not records:
        return (), ("snapshot_population",)
    blocked: list[str] = []
    records_by_event_id: dict[str, PipelineSnapshotAuthorityRecord] = {}
    for index, record in enumerate(records):
        absent = record.missing_boundaries(f"snapshot[{index}]")
        if absent:
            blocked.extend(absent)
            continue
        try:
            record.validate()
        except ValueError as error:
            blocked.append(f"snapshot[{index}].invalid:{error}")
            continue
        assert record.event_id is not None
        existing = records_by_event_id.get(record.event_id)
        if existing is not None:
            if existing != record:
                blocked.append(f"snapshot[{index}].event_id_conflict")
            continue
        records_by_event_id[record.event_id] = record
    return tuple(records_by_event_id.values()), tuple(sorted(set(blocked)))


def _optional_integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_float(value: object) -> float | None:
    return value if isinstance(value, float) and math.isfinite(value) else None


def _nearest_rank_percentile(values: Sequence[int | float], percentile: float) -> float | None:
    if not values:
        return None
    rank = math.ceil(percentile * len(values))
    return float(sorted(values)[rank - 1])

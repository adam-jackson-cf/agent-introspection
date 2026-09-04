"""Fail-closed live executor for dashboard pipeline proofs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import sleep
from typing import Any, Protocol, cast
from urllib.parse import urlparse

from agent_introspection.config import AppConfig, load_config
from agent_introspection.source import ClickHouseClient
from agent_introspection.telemetry import (
    DerivedEvent,
    drain_outbox_event_ids,
    enqueue_events,
    remote_event_ids,
)
from experiments.dashboard_prototype.contracts import EvidenceProvenance, ExperimentResult
from experiments.dashboard_prototype.pipeline_common import (
    PipelineExecutionAuthority,
    PipelineExperimentProof,
)
from experiments.dashboard_prototype.pipeline_ledger import (
    LedgerRemotePopulationAuthority,
    LedgerRemoteResult,
    build_ledger_remote_query,
    parse_ledger_remote_result,
    reconcile_ledger_remote,
)
from experiments.dashboard_prototype.pipeline_live_common import (
    LiveExperimentEvidence,
    LiveProofRequest,
    RemoteCalculationPrimitive,
)
from experiments.dashboard_prototype.pipeline_live_integrity import extract as extract_integrity
from experiments.dashboard_prototype.pipeline_live_ledger import (
    LedgerMaintenancePrimitive,
)
from experiments.dashboard_prototype.pipeline_live_ledger import (
    extract as extract_ledger,
)
from experiments.dashboard_prototype.pipeline_live_outbox import (
    E5DeliveryAttemptPrimitive,
    E5FinalDrainPrimitive,
    E5OutboxEventPrimitive,
    OutboxEventStatus,
)
from experiments.dashboard_prototype.pipeline_live_outbox import (
    extract as extract_outbox,
)
from experiments.dashboard_prototype.pipeline_live_snapshot import extract as extract_snapshot
from experiments.dashboard_prototype.pipeline_live_source_lag import extract as extract_source_lag
from experiments.dashboard_prototype.pipeline_live_terminal_cadence import (
    extract as extract_terminal_cadence,
)
from experiments.dashboard_prototype.pipeline_outbox import (
    E5_REMOTE_SQL,
    DeliveryAttempt,
    E5RemotePopulation,
    OutboxEvent,
    e5_local_oracle,
    parse_e5_remote_result,
)
from experiments.dashboard_prototype.pipeline_snapshot import (
    A01_LATEST_PIPELINE_SNAPSHOT_SQL,
    A02_SCAN_OUTCOMES_SQL,
    A05_SCAN_WORKLOAD_DURATION_SQL,
    A01LatestPipelineSnapshot,
    A02ScanOutcome,
    A05ScanWorkloadDuration,
    PipelineSnapshotAuthorityRecord,
    ScanErrorClass,
    ScanTerminalClass,
    SnapshotPopulationCounts,
    oracle_a01_latest_pipeline_snapshot,
    oracle_a02_scan_outcomes,
    oracle_a05_scan_workload_duration,
    parse_a01_latest_pipeline_snapshot_row,
    parse_a02_scan_outcome_row,
    parse_a05_scan_workload_duration_row,
)
from experiments.dashboard_prototype.pipeline_terminal_cadence import (
    A03_TERMINAL_OUTCOME_SQL,
    A04_TERMINAL_CADENCE_SQL,
    A03TerminalOutcomeResult,
    TerminalObservation,
    TerminalSchedulePolicy,
    oracle_a03_terminal_outcomes,
    parse_a03_terminal_outcome_result,
    parse_a04_terminal_cadence_result,
)

NAMESPACE = "agent-introspection.dashboard-prototype.v1"
EVENT_NAME = "dashboard_prototype.pipeline_snapshot.v1"
EXPERIMENT_IDS = tuple(f"E-Pipeline-{number}" for number in range(1, 7))
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z", re.ASCII)
_SOURCE_LAG_QUERY_ID = "pipeline-source-lag-v1"
_INTEGRITY_QUERY_ID = "p11-6b2487cf6f5f395b3409d2de"

_SOURCE_LAG_SQL = """
SELECT attributes_string['dashboard.cohort'] AS cohort,
 toUInt32(count()) AS population,
 toUInt32(countIf(attributes_string['dashboard.disposition'] = 'accepted')) AS accepted,
 toUInt32(countIf(
     attributes_string['dashboard.disposition'] = 'missing-capability'
 )) AS missing_capability,
 toUInt32(countIf(attributes_string['dashboard.disposition'] = 'rejected')) AS rejected,
 toUInt32(countIf(attributes_string['dashboard.disposition'] = 'duplicate')) AS duplicate,
 toUInt32(countIf(
     attributes_string['dashboard.disposition'] = 'accepted'
     AND attributes_number['dashboard.negative_skew'] = 0
 )) AS n,
 toFloat64(if(n = 0, 0., quantileExactInclusive(0.5)(if(
     attributes_string['dashboard.disposition'] = 'accepted'
     AND attributes_number['dashboard.negative_skew'] = 0,
     attributes_number['dashboard.lag_seconds'],
     NULL
 )))) AS p50_lag_seconds,
 toFloat64(if(n = 0, 0., arrayElement(arraySort(groupArrayIf(
     attributes_number['dashboard.lag_seconds'],
     attributes_string['dashboard.disposition'] = 'accepted'
     AND attributes_number['dashboard.negative_skew'] = 0
 )),
     toUInt64(greatest(1, ceil(n * 0.95)))))) AS p95_lag_seconds,
 toUInt32(sumIf(attributes_number['dashboard.negative_skew'],
       attributes_string['dashboard.disposition'] = 'accepted')) AS negative_skew_count
FROM signoz_logs.distributed_logs_v2
WHERE timestamp > {start_ns:UInt64} AND timestamp <= {end_ns:UInt64}
 AND ts_bucket_start BETWEEN {start_bucket:UInt64} AND {end_bucket:UInt64}
 AND resource.`service.name`::String = 'agent-introspection'
 AND attributes_string['event.name'] = {event_name:String}
 AND attributes_string['dashboard.event_kind'] = 'primitive'
 AND attributes_string['dashboard.experiment_id'] = 'E-Pipeline-3'
 AND attributes_string['dashboard.run_id_hash'] = {run_id_hash:String}
 AND attributes_string['dashboard.query_id'] = {query_id:String}
 AND attributes_string['event.id'] IN ({event_ids})
GROUP BY cohort ORDER BY cohort
""".strip()
_INTEGRITY_SQL = """
SELECT attributes_string['dashboard.metric'] AS metric,
 attributes_string['dashboard.withheld'] AS withheld,
 toUInt32(count()) AS incident_rows,
 sumIf(attributes_number['dashboard.value'],
       attributes_string['dashboard.withheld'] = 'false') AS value
FROM signoz_logs.distributed_logs_v2
WHERE timestamp > {start_ns:UInt64} AND timestamp <= {end_ns:UInt64}
 AND ts_bucket_start BETWEEN {start_bucket:UInt64} AND {end_bucket:UInt64}
 AND resource.`service.name`::String = 'agent-introspection'
 AND attributes_string['event.name'] = {event_name:String}
 AND attributes_string['dashboard.event_kind'] = 'primitive'
 AND attributes_string['dashboard.experiment_id'] = 'E-Pipeline-4'
 AND attributes_string['dashboard.run_id_hash'] = {run_id_hash:String}
 AND attributes_string['dashboard.query_id'] = {query_id:String}
 AND attributes_string['event.id'] IN ({event_ids})
GROUP BY metric, withheld ORDER BY metric, withheld
""".strip()


class PipelineExecutionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ExtractionWindow:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None or self.start >= self.end:
            raise PipelineExecutionError("window must be ordered timezone-aware instants")

    def identity(self) -> str:
        return f"{self.start.astimezone(UTC).isoformat()}..{self.end.astimezone(UTC).isoformat()}"


@dataclass(frozen=True, slots=True)
class _EventSpec:
    kind: str
    query_id: str
    ordinal: int
    attributes: Mapping[str, str | int | float | bool]


@dataclass(frozen=True, slots=True)
class RunEnvelope:
    namespace: str
    run_id: str
    window: ExtractionWindow
    proofs: tuple[PipelineExperimentProof, ...]
    primitive_event_ids: tuple[str, ...]
    result_event_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        proof_by_id = {proof.experiment_id.value: proof for proof in self.proofs}
        if tuple(proof_by_id) != EXPERIMENT_IDS or tuple(self.authority) != EXPERIMENT_IDS:
            raise PipelineExecutionError("run envelope authority population is incomplete")
        if len(self.result_event_ids) != len(self.proofs):
            raise PipelineExecutionError("run envelope result event population is incomplete")
        for experiment, proof in proof_by_id.items():
            binding = self.authority.get(experiment)
            if binding is None or binding.proof != proof:
                raise PipelineExecutionError("run envelope authority binding does not match proof")
            if proof.result is ExperimentResult.PROVEN and (
                binding.local_oracle != binding.remote_result
                or not binding.primitive_event_ids
                or binding.result_event_id not in self.result_event_ids
            ):
                raise PipelineExecutionError("proven proof lacks a matching authority binding")

    local_oracle: Mapping[str, Mapping[str, object]]
    remote_result: Mapping[str, Mapping[str, object]]
    drain: Mapping[str, int]
    authority: Mapping[str, PipelineExecutionAuthority] = field(default_factory=dict)

    def payload(self) -> dict[str, object]:
        ids = self.primitive_event_ids + self.result_event_ids
        return {
            "namespace": self.namespace,
            "run_id": self.run_id,
            "window": {
                "start": self.window.start.astimezone(UTC).isoformat(),
                "end": self.window.end.astimezone(UTC).isoformat(),
            },
            "proofs": [json.loads(p.canonical_json()) for p in self.proofs],
            "domain_local_oracle": _plain(self.local_oracle),
            "remote_result": _plain(self.remote_result),
            "provenance": EvidenceProvenance.FRESH_REAL.value,
            "hashes": {"run_id_hash": canonical_hash(self.run_id)},
            "exact_source_boundary": self.window.identity(),
            "event_map": {
                "primitive": list(self.primitive_event_ids),
                "result": list(self.result_event_ids),
            },
            "drain": dict(self.drain),
            "authority": {
                experiment: binding.payload()
                for experiment, binding in sorted(self.authority.items())
            },
            "cleanup_selector": {
                "namespace": self.namespace,
                "run_id": self.run_id,
                "event_ids": list(ids),
            },
        }

    def canonical_json(self) -> str:
        return canonical_json(self.payload())


def canonical_json(value: object) -> str:
    _reject_unsafe(value)
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def canonical_hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
        raise PipelineExecutionError("run ID is not exact and bounded")
    return run_id


def validate_output_path(path: Path) -> Path:
    root = (Path(__file__).parent / "evidence").resolve()
    candidate = path.resolve(strict=False)
    if candidate.suffix != ".json" or root not in candidate.parents:
        raise PipelineExecutionError("output must be a JSON file under prototype evidence")
    return candidate


def validate_loopback_endpoint(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise PipelineExecutionError("telemetry endpoint must be loopback HTTP")
    return endpoint.rstrip("/")


def extract_live(
    connection: sqlite3.Connection, request: LiveProofRequest
) -> tuple[LiveExperimentEvidence, ...]:
    evidence = (
        extract_snapshot(connection, request),
        extract_terminal_cadence(connection, request),
        extract_source_lag(connection, request),
        extract_integrity(connection, request),
        extract_outbox(connection, request),
        extract_ledger(connection, request),
    )
    if tuple(item.proof.experiment_id.value for item in evidence) != EXPERIMENT_IDS:
        raise PipelineExecutionError("typed proof ordering is incomplete")
    return evidence


def _event(
    proof: PipelineExperimentProof,
    run_id: str,
    window: ExtractionWindow,
    spec: _EventSpec,
    *,
    timestamp_ns: int | None = None,
) -> DerivedEvent:
    proof_hash = proof.content_hash()
    entity = canonical_hash(
        {
            "namespace": NAMESPACE,
            "run_id_hash": canonical_hash(run_id),
            "experiment_id": proof.experiment_id.value,
            "query_id": spec.query_id,
            "ordinal": spec.ordinal,
            "proof_hash": proof_hash,
            "kind": spec.kind,
        }
    )
    return DerivedEvent(
        NAMESPACE,
        entity,
        1,
        spec.ordinal,
        EVENT_NAME,
        {
            "dashboard.event_kind": spec.kind,
            "dashboard.experiment_id": proof.experiment_id.value,
            "dashboard.query_id": spec.query_id,
            "dashboard.run_id_hash": canonical_hash(run_id),
            "dashboard.proof_hash": proof_hash,
            "dashboard.source_boundary_hash": canonical_hash(proof.source_boundary),
            **dict(spec.attributes),
        },
        int(window.end.timestamp() * 1_000_000_000) if timestamp_ns is None else timestamp_ns,
    )


def primitive_events(
    evidence: Sequence[LiveExperimentEvidence], run_id: str, window: ExtractionWindow
) -> tuple[DerivedEvent, ...]:
    events: list[DerivedEvent] = []
    for item in evidence:
        if not item.primitives:
            continue
        if item.remote_query_id is None:
            raise PipelineExecutionError("pipeline calculation inputs lack a remote query")
        for primitive in item.primitives:
            attrs, timestamp_ns = _primitive_attributes(item.proof, primitive)
            ordinal = _primitive_ordinal(primitive, len(events))
            events.append(
                _event(
                    item.proof,
                    run_id,
                    window,
                    _EventSpec("primitive", item.remote_query_id, ordinal, attrs),
                    timestamp_ns=timestamp_ns,
                )
            )
    return tuple(events)


def _primitive_ordinal(primitive: object, fallback: int) -> int:
    return primitive.ordinal if isinstance(primitive, RemoteCalculationPrimitive) else fallback


def _primitive_attributes(
    proof: PipelineExperimentProof, primitive: object
) -> tuple[dict[str, str | int | float | bool], int]:

    if isinstance(primitive, RemoteCalculationPrimitive):
        source_time = primitive.source_time
        attrs: dict[str, str | int | float | bool] = {
            f"dashboard.{key}": value for key, value in primitive.dimensions.items()
        }
        attrs.update({f"dashboard.{key}": value for key, value in primitive.measures.items()})
        if proof.experiment_id.value == "E-Pipeline-1":
            attrs["dashboard.completed_at_ns"] = str(primitive.measures["completed_at_ns"])
        if proof.experiment_id.value == "E-Pipeline-4":
            attrs["dashboard.metric"] = str(attrs.get("dashboard.metric", "zero_population_marker"))
            attrs["dashboard.withheld"] = str(attrs.get("dashboard.withheld", "false"))
            attrs["dashboard.value"] = next(iter(primitive.measures.values()))
        return attrs, int(source_time.timestamp() * 1_000_000_000)
    if isinstance(primitive, E5OutboxEventPrimitive):
        destination = primitive.destination
        event_type = primitive.event_type
        return {
            "dashboard.outbox_event_id": primitive.event_id,
            "dashboard.destination": destination,
            "dashboard.outbox_event_type": event_type,
            "dashboard.created_at_ns": int(primitive.created_at.timestamp() * 1_000_000_000),
            "dashboard.is_pending": primitive.status is OutboxEventStatus.PENDING,
        }, int(primitive.created_at.timestamp() * 1_000_000_000)
    if isinstance(primitive, E5DeliveryAttemptPrimitive):
        return {
            "dashboard.attempt_id": primitive.attempt_id,
            "dashboard.outbox_event_id": primitive.event_id,
            "dashboard.drain_id": primitive.drain_id,
            "dashboard.status": primitive.status.value,
            "dashboard.error_class": primitive.error_class or "none",
        }, int(primitive.attempted_at.timestamp() * 1_000_000_000)
    if isinstance(primitive, E5FinalDrainPrimitive):
        return {"dashboard.drain_id": primitive.drain_id}, int(
            primitive.completed_at.timestamp() * 1_000_000_000
        )
    if isinstance(primitive, LedgerMaintenancePrimitive):
        return {
            "dashboard.source_event_id": primitive.event_id,
            "dashboard.database_identity": primitive.database_identity,
            "dashboard.check_type": primitive.check_type.value,
            "dashboard.result": primitive.result.value,
            "dashboard.backup_completed_at_ns": int(
                primitive.backup_completed_at.timestamp() * 1_000_000_000
            ),
            "dashboard.database_bytes": primitive.database_bytes,
            "dashboard.wal_bytes": primitive.wal_bytes,
            "dashboard.freelist_count": primitive.freelist_count,
            "dashboard.page_count": primitive.page_count,
            "dashboard.migration_state": primitive.migration_state.value,
            "dashboard.runtime_host": primitive.runtime_host,
        }, int(primitive.completed_at.timestamp() * 1_000_000_000)
    raise PipelineExecutionError("unknown immutable primitive authority")


def result_events(
    proofs: Sequence[PipelineExperimentProof], run_id: str, window: ExtractionWindow
) -> tuple[DerivedEvent, ...]:
    return tuple(
        _event(
            proof,
            run_id,
            window,
            _EventSpec(
                "result",
                "result-v1",
                ordinal,
                {
                    "dashboard.result": proof.result.value,
                    "dashboard.source_boundary_hash": canonical_hash(proof.source_boundary),
                    "dashboard.evidence_count": len(proof.evidence_ids),
                    "dashboard.blocked_count": len(proof.blocked_boundaries),
                    "dashboard.assertion_count": len(proof.assertions),
                    "dashboard.metric_count": len(proof.metrics),
                },
            ),
        )
        for ordinal, proof in enumerate(proofs, 1)
    )


class _RemoteClient(Protocol):
    def query(
        self, sql: str, parameters: Mapping[str, str | int]
    ) -> Iterable[Mapping[str, Any]]: ...


def _exact_drain(
    connection: sqlite3.Connection, events: Sequence[DerivedEvent], endpoint: str
) -> Mapping[str, int]:
    result = drain_outbox_event_ids(
        connection,
        [event.event_id for event in events],
        endpoint=f"{endpoint}/v1/logs",
        include_delivered=True,
    )
    if (
        not isinstance(result, Mapping)
        or any(
            isinstance(result.get(key), bool) or not isinstance(result.get(key), int)
            for key in ("selected", "delivered", "pending")
        )
        or (result["selected"], result["delivered"], result["pending"])
        != (len(events), len(events), 0)
    ):
        raise PipelineExecutionError("exact outbox drain mismatch")
    return {key: int(result[key]) for key in ("selected", "delivered", "pending")}


def _verify_ids(client: _RemoteClient, events: Sequence[DerivedEvent]) -> None:
    expected = {event.event_id for event in events}
    for attempt in range(20):
        if remote_event_ids(client, events) == expected:
            return
        if attempt < 19:
            sleep(0.5)
    raise PipelineExecutionError("exact remote event IDs mismatch")


def _params(
    events: Sequence[DerivedEvent], window: ExtractionWindow, query_id: str, run_id: str
) -> tuple[str, dict[str, str | int]]:
    placeholders = ", ".join(f"{{event_{index}:String}}" for index in range(len(events)))
    params: dict[str, str | int] = {
        "start_ns": int(window.start.timestamp() * 1e9),
        "end_ns": int(window.end.timestamp() * 1e9),
        "start_bucket": max(0, int(window.start.timestamp()) - 1_800),
        "end_bucket": int(window.end.timestamp()),
        "event_name": EVENT_NAME,
        "run_id_hash": canonical_hash(run_id),
        "query_id": query_id,
    }
    params.update({f"event_{index}": event.event_id for index, event in enumerate(events)})
    return placeholders, params


def _direct_params(
    events: Sequence[DerivedEvent], window: ExtractionWindow
) -> tuple[str, dict[str, str | int]]:
    placeholders = ", ".join(f"{{event_{index}:String}}" for index in range(len(events)))
    return placeholders, {
        "start": window.start.astimezone(UTC).isoformat(),
        "end": window.end.astimezone(UTC).isoformat(),
        **{f"event_{index}": event.event_id for index, event in enumerate(events)},
    }


def _snapshot_remote(
    client: _RemoteClient, events: Sequence[DerivedEvent], window: ExtractionWindow
) -> dict[str, object]:
    placeholders, params = _direct_params(events, window)
    a01_rows = list(
        client.query(A01_LATEST_PIPELINE_SNAPSHOT_SQL.replace("{event_ids}", placeholders), params)
    )
    if len(a01_rows) != 1:
        raise PipelineExecutionError("A01 remote calculation must return exactly one row")
    a01 = parse_a01_latest_pipeline_snapshot_row(a01_rows[0])
    a01.validate()
    a02 = tuple(
        parse_a02_scan_outcome_row(row)
        for row in client.query(A02_SCAN_OUTCOMES_SQL.replace("{event_ids}", placeholders), params)
    )
    if not a02:
        raise PipelineExecutionError("A02 remote calculation has no terminal population")
    if len({row.terminal_class for row in a02}) != len(a02):
        raise PipelineExecutionError("A02 remote calculation repeats a terminal class")
    a05_rows = list(
        client.query(A05_SCAN_WORKLOAD_DURATION_SQL.replace("{event_ids}", placeholders), params)
    )
    if len(a05_rows) != 1:
        raise PipelineExecutionError("A05 remote calculation must return exactly one row")
    a05 = parse_a05_scan_workload_duration_row(a05_rows[0])
    return {
        "a01": _a01_plain(oracle_a01_latest_pipeline_snapshot((a01,)).calculation),
        "a02": _a02_plain(a02),
        "a05": _a05_plain(a05),
    }


def _a01_plain(value: A01LatestPipelineSnapshot | None) -> dict[str, object]:
    if value is None:
        raise PipelineExecutionError("A01 remote calculation is incomplete")
    return {
        "event_id": value.event_id,
        "completed_at_ns": value.completed_at_ns,
        "payload_schema_version": value.payload_schema_version,
        "terminal_class": value.terminal_class.value,
        "duration_ms": value.duration_ms,
        "error_class": value.error_class.value,
        "rows": value.counts.rows,
        "logs": value.counts.logs,
        "traces": value.counts.traces,
        "context_events": value.counts.context_events,
        "canonical_activities": value.counts.canonical_activities,
        "source_sessions": value.counts.source_sessions,
        "pending_outbox": value.counts.pending_outbox,
        "failed_during_drain": value.counts.failed_during_drain,
        "bounded_drain_id": value.bounded_drain_id,
        "rows_per_second": value.rows_per_second,
    }


def _a02_plain(rows: Sequence[A02ScanOutcome]) -> dict[str, object]:
    return {
        "outcomes": {
            row.terminal_class.value: {
                "terminal_scan_count": row.terminal_scan_count,
                "terminal_scan_percent": row.terminal_scan_percent,
            }
            for row in rows
        },
        "terminal_scan_count": sum(row.terminal_scan_count for row in rows),
    }


def _a05_plain(value: A05ScanWorkloadDuration) -> dict[str, object]:
    return {
        "successful_scan_count": value.successful_scan_count,
        "positive_duration_scan_count": value.positive_duration_scan_count,
        "duration_p50_ms": value.duration_p50_ms,
        "duration_p95_ms": value.duration_p95_ms,
        "rows_p50": value.rows_p50,
        "rows_p95": value.rows_p95,
        "rows_per_second_p50": value.rows_per_second_p50,
        "rows_per_second_p95": value.rows_per_second_p95,
    }


def _snapshot_records(item: LiveExperimentEvidence) -> tuple[PipelineSnapshotAuthorityRecord, ...]:
    records: list[PipelineSnapshotAuthorityRecord] = []
    for primitive in item.primitives:
        dimensions = primitive.dimensions
        measures = primitive.measures
        try:
            record = PipelineSnapshotAuthorityRecord(
                event_id=cast(str | None, dimensions.get("event_id")),
                completed_at_ns=cast(int | None, measures.get("completed_at_ns")),
                payload_schema_version=cast(int | None, measures.get("payload_schema_version")),
                terminal_class=ScanTerminalClass(dimensions["terminal_class"]),
                duration_ms=cast(int | None, measures.get("duration_ms")),
                error_class=ScanErrorClass(dimensions["error_class"]),
                counts=SnapshotPopulationCounts(
                    cast(int | None, measures.get("rows")),
                    cast(int | None, measures.get("logs")),
                    cast(int | None, measures.get("traces")),
                    cast(int | None, measures.get("context_events")),
                    cast(int | None, measures.get("canonical_activities")),
                    cast(int | None, measures.get("source_sessions")),
                    cast(int | None, measures.get("pending_outbox")),
                    cast(int | None, measures.get("failed_during_drain")),
                ),
                bounded_drain_id=cast(str | None, dimensions.get("bounded_drain_id")),
            )
            record.validate()
        except (KeyError, TypeError, ValueError) as error:
            raise PipelineExecutionError("snapshot primitive authority is incomplete") from error
        records.append(record)
    return tuple(records)


def _snapshot_local(item: LiveExperimentEvidence) -> dict[str, object]:
    records = _snapshot_records(item)
    a01 = oracle_a01_latest_pipeline_snapshot(records)
    a02 = oracle_a02_scan_outcomes(records)
    a05 = oracle_a05_scan_workload_duration(records)
    if a01.blocked_boundaries or a02.blocked_boundaries or a05.blocked_boundaries:
        raise PipelineExecutionError("snapshot local authority is incomplete")
    assert a01.calculation is not None
    assert a05.calculation is not None
    return {
        "a01": _a01_plain(a01.calculation),
        "a02": _a02_plain(a02.outcomes),
        "a05": _a05_plain(a05.calculation),
    }


def _cadence_remote(
    client: _RemoteClient, events: Sequence[DerivedEvent], window: ExtractionWindow
) -> dict[str, object]:
    placeholders, params = _direct_params(events, window)
    a03_rows = list(
        client.query(A03_TERMINAL_OUTCOME_SQL.replace("{event_ids}", placeholders), params)
    )
    a04_rows = list(
        client.query(A04_TERMINAL_CADENCE_SQL.replace("{event_ids}", placeholders), params)
    )
    if len(a03_rows) != 1 or len(a04_rows) != 1:
        raise PipelineExecutionError(
            "terminal cadence remote calculation must return one row per query"
        )
    a03 = parse_a03_terminal_outcome_result(a03_rows[0])
    a04 = parse_a04_terminal_cadence_result(a04_rows[0])
    return {
        "a03": {
            "succeeded_count": a03.succeeded_count,
            "failed_count": a03.failed_count,
            "no_data_count": a03.no_data_count,
            "observed_count": a03.observed_count,
            "unclassified_terminal_state_count": a03.unclassified_terminal_state_count,
        },
        "a04": {
            "policy_identity": a04.policy_identity,
            "interval_seconds": a04.interval_seconds,
            "timezone": a04.timezone,
            "anchor_time": a04.anchor_time,
            "latest_successful_completion": (
                a04.latest_successful_completion.astimezone(UTC).isoformat()
                if a04.latest_successful_completion is not None
                else None
            ),
            "policy_identity_count": a04.policy_identity_count,
            "interval_seconds_count": a04.interval_seconds_count,
            "timezone_count": a04.timezone_count,
            "anchor_time_count": a04.anchor_time_count,
        },
    }


def _number(value: object, *, integral: bool) -> int | float:
    if isinstance(value, bool):
        raise PipelineExecutionError("remote calculation returned an incorrectly typed scalar")
    if integral:
        if type(value) is not int:
            raise PipelineExecutionError("remote calculation returned an incorrectly typed scalar")
        return value
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PipelineExecutionError("remote calculation returned an incorrectly typed scalar")
    return float(value)


def remote_calculations(
    client: _RemoteClient,
    primitives: Sequence[DerivedEvent],
    window: ExtractionWindow,
    run_id: str,
    *,
    evidence: Sequence[LiveExperimentEvidence] = (),
) -> dict[str, Mapping[str, object]]:
    grouped = {
        experiment: tuple(
            event
            for event in primitives
            if event.attributes["dashboard.experiment_id"] == experiment
        )
        for experiment in EXPERIMENT_IDS
    }
    evidence_by_id = {item.proof.experiment_id.value: item for item in evidence}
    output: dict[str, Mapping[str, object]] = {}
    if grouped["E-Pipeline-1"]:
        output["E-Pipeline-1"] = _snapshot_remote(client, grouped["E-Pipeline-1"], window)
    if grouped["E-Pipeline-2"]:
        output["E-Pipeline-2"] = _cadence_remote(client, grouped["E-Pipeline-2"], window)
    source = grouped["E-Pipeline-3"]
    if source:
        placeholders, params = _params(source, window, _SOURCE_LAG_QUERY_ID, run_id)
        rows = list(client.query(_SOURCE_LAG_SQL.replace("{event_ids}", placeholders), params))
        keys = (
            "population",
            "accepted",
            "missing_capability",
            "rejected",
            "duplicate",
            "n",
            "p50_lag_seconds",
            "p95_lag_seconds",
            "negative_skew_count",
        )
        output["E-Pipeline-3"] = {
            str(row["cohort"]): {
                key: _number(
                    row.get(key), integral=key not in {"p50_lag_seconds", "p95_lag_seconds"}
                )
                for key in keys
            }
            for row in rows
        }
    if grouped["E-Pipeline-4"]:
        output["E-Pipeline-4"] = _remote_integrity_counts(
            client, grouped["E-Pipeline-4"], window, run_id
        )
    if "E-Pipeline-5" in evidence_by_id:
        outbox = _outbox_remote(client, evidence_by_id["E-Pipeline-5"])
        if outbox is not None:
            output["E-Pipeline-5"] = outbox
    if grouped["E-Pipeline-6"]:
        ledger = _ledger_remote(client, grouped["E-Pipeline-6"], window, run_id)
        if ledger is not None:
            output["E-Pipeline-6"] = ledger
    return output


def _outbox_population(
    item: LiveExperimentEvidence,
) -> tuple[tuple[OutboxEvent, ...], tuple[DeliveryAttempt, ...], E5RemotePopulation] | None:
    selected: list[OutboxEvent] = []
    attempts: list[DeliveryAttempt] = []
    drains: list[E5FinalDrainPrimitive] = []
    for row in item.primitives:
        if isinstance(row, E5OutboxEventPrimitive):
            selected.append(
                OutboxEvent(
                    row.event_id,
                    row.created_at,
                    row.destination,
                    row.event_type,
                    row.status is OutboxEventStatus.PENDING,
                )
            )
        elif isinstance(row, E5DeliveryAttemptPrimitive):
            attempts.append(
                DeliveryAttempt(
                    row.attempt_id,
                    row.event_id,
                    row.drain_id,
                    row.attempted_at,
                    row.status,
                    row.error_class,
                )
            )
        elif isinstance(row, E5FinalDrainPrimitive):
            drains.append(row)
    events = tuple(selected)
    if len(drains) != 1 or not events:
        return None
    drain = drains[0]
    return (
        events,
        tuple(attempts),
        E5RemotePopulation(
            tuple(row.event_id for row in events),
            drain.drain_id,
            min(row.created_at for row in events),
            max(row.created_at for row in events),
            drain.completed_at,
        ),
    )


def _outbox_plain(value: object) -> dict[str, object]:
    return {
        field: getattr(value, field)
        for field in (
            "selected_event_count",
            "pending_event_count",
            "attempted_event_count",
            "failed_event_count",
            "final_drain_attempt_count",
            "oldest_pending_age_seconds",
            "drain_failure_percentage",
        )
    }


def _outbox_parameters(parameters: Mapping[str, object]) -> dict[str, str | int]:
    event_ids = parameters.get("event_ids")
    final_drain_id = parameters.get("final_drain_id")
    source_start = parameters.get("source_start")
    source_end = parameters.get("source_end")
    scan_completed_at = parameters.get("scan_completed_at")
    if (
        not isinstance(event_ids, tuple)
        or not all(isinstance(event_id, str) for event_id in event_ids)
        or not isinstance(final_drain_id, str)
        or not isinstance(source_start, datetime)
        or not isinstance(source_end, datetime)
        or not isinstance(scan_completed_at, datetime)
    ):
        raise PipelineExecutionError("outbox remote authority parameters are invalid")
    return {
        "event_ids": json.dumps(event_ids),
        "final_drain_id": final_drain_id,
        "source_start": source_start.astimezone(UTC).isoformat(),
        "source_end": source_end.astimezone(UTC).isoformat(),
        "scan_completed_at": scan_completed_at.astimezone(UTC).isoformat(),
    }


def _outbox_remote(client: _RemoteClient, item: LiveExperimentEvidence) -> dict[str, object] | None:
    population = _outbox_population(item)
    if population is None or (parameters := population[2].sql_parameters()) is None:
        return None
    return _outbox_plain(
        parse_e5_remote_result(list(client.query(E5_REMOTE_SQL, _outbox_parameters(parameters))))
    )


def _ledger_plain(rows: Sequence[LedgerRemoteResult]) -> dict[str, object]:
    return {
        "selected": {
            row.event_id: {
                "database_identity": row.database_identity,
                "check_type": row.check_type.value,
                "completed_at_ns": int(row.completed_at.timestamp() * 1_000_000_000),
                "result": row.result.value,
                "backup_completed_at_ns": int(row.backup_completed_at.timestamp() * 1_000_000_000),
                "database_bytes": row.database_bytes,
                "wal_bytes": row.wal_bytes,
                "freelist_count": row.freelist_count,
                "page_count": row.page_count,
                "migration_state": row.migration_state.value,
                "runtime_host": row.runtime_host,
            }
            for row in rows
        }
    }


def _ledger_remote(
    client: _RemoteClient, events: Sequence[DerivedEvent], window: ExtractionWindow, run_id: str
) -> dict[str, object] | None:
    query = build_ledger_remote_query(
        LedgerRemotePopulationAuthority(
            tuple(event.event_id for event in events), window.start, window.end
        ),
        start_bucket=int(window.start.timestamp()),
        end_bucket=int(window.end.timestamp()),
        event_name=EVENT_NAME,
        run_id_hash=canonical_hash(run_id),
    )
    return (
        None
        if query is None
        else _ledger_plain(
            parse_ledger_remote_result(list(client.query(query.sql, query.parameters)))
        )
    )


def _remote_integrity_counts(
    client: _RemoteClient,
    integrity: Sequence[DerivedEvent],
    window: ExtractionWindow,
    run_id: str,
) -> dict[str, int]:
    placeholders, params = _params(integrity, window, _INTEGRITY_QUERY_ID, run_id)
    rows = list(client.query(_INTEGRITY_SQL.replace("{event_ids}", placeholders), params))
    expected_rows: dict[tuple[str, str], int] = {}
    for event in integrity:
        metric = event.attributes.get("dashboard.metric")
        withheld = event.attributes.get("dashboard.withheld")
        if (
            not isinstance(metric, str)
            or not isinstance(withheld, str)
            or withheld not in {"true", "false"}
        ):
            raise PipelineExecutionError("integrity primitive dimensions are invalid")
        key = (metric, withheld)
        expected_rows[key] = expected_rows.get(key, 0) + 1
    observed_rows: dict[tuple[str, str], int] = {}
    counts: dict[str, int] = {}
    for row in rows:
        remote_metric = row.get("metric")
        remote_withheld = row.get("withheld")
        if (
            not isinstance(remote_metric, str)
            or not remote_metric
            or not isinstance(remote_withheld, str)
            or remote_withheld not in {"true", "false"}
            or (remote_metric, remote_withheld) in observed_rows
        ):
            raise PipelineExecutionError("integrity remote metric mismatch")
        incident_rows = _number(row.get("incident_rows"), integral=True)
        numeric = _number(row.get("value"), integral=True)
        if not isinstance(incident_rows, int) or not isinstance(numeric, int):
            raise PipelineExecutionError("integrity remote metric mismatch")
        observed_rows[(remote_metric, remote_withheld)] = incident_rows
        if remote_withheld == "false":
            counts[remote_metric] = counts.get(remote_metric, 0) + numeric
    if observed_rows != expected_rows:
        raise PipelineExecutionError("integrity remote incident population mismatch")
    sentinel = counts.pop("zero_population_marker", None)
    if sentinel is not None and (sentinel != 0 or len(expected_rows) != 1):
        raise PipelineExecutionError("integrity zero-population sentinel mismatch")
    if not counts and sentinel != 0:
        raise PipelineExecutionError("integrity zero-population sentinel missing")
    return counts


def _final_proofs(
    evidence: Sequence[LiveExperimentEvidence],
    remote: Mapping[str, Mapping[str, object]],
    local_oracle: Mapping[str, Mapping[str, object]],
) -> tuple[PipelineExperimentProof, ...]:
    proofs: list[PipelineExperimentProof] = []
    for item in evidence:
        proof = item.proof
        experiment = proof.experiment_id.value
        expected = local_oracle.get(experiment)
        complete = (
            bool(proof.evidence_ids)
            and bool(item.primitives)
            and item.remote_query_id is not None
            and experiment in remote
            and expected is not None
        )
        if proof.result is ExperimentResult.PROVEN and not complete:
            proof = replace(
                proof,
                result=ExperimentResult.BLOCKED,
                blocked_boundaries=tuple(
                    sorted(
                        set(proof.blocked_boundaries)
                        | {f"remote calculation:{proof.experiment_id.value}"}
                    )
                ),
            )
        elif proof.result is ExperimentResult.PROVEN:
            reconciled = _typed_equal(remote[experiment], expected)
            proof = replace(
                proof,
                result=ExperimentResult.PROVEN if reconciled else ExperimentResult.FAILED,
                assertions={
                    **proof.assertions,
                    "remote_calculation_reconciled": reconciled,
                },
            )
        proofs.append(proof)
    return tuple(proofs)


def _integrity_oracle(
    proof: PipelineExperimentProof, item: LiveExperimentEvidence
) -> dict[str, int]:
    expected = {str(key): value for key, value in proof.metrics.items() if type(value) is int}
    if expected and all(key.startswith("p11.") for key in expected):
        return expected
    return {
        str(primitive.dimensions["metric"]): int(next(iter(primitive.measures.values())))
        for primitive in item.primitives
        if primitive.dimensions.get("withheld") == "false"
    }


def _cadence_local(item: LiveExperimentEvidence) -> dict[str, object] | None:
    rows = tuple(
        TerminalObservation(
            cast(str, row.dimensions["durable_id_hash"]),
            cast(str, row.dimensions["terminal_state"]),
            row.source_time,
            datetime.fromisoformat(cast(str, row.dimensions["scheduled_at"])),
        )
        for row in item.primitives
    )
    if not rows:
        return None
    first = item.primitives[0]
    policy = TerminalSchedulePolicy(
        cast(str, first.dimensions["schedule_policy_identity"]),
        timedelta(seconds=cast(int, first.measures["schedule_interval_seconds"])),
        cast(str, first.dimensions["schedule_timezone"]),
        datetime.fromisoformat(cast(str, first.dimensions["schedule_anchor_at"])),
    )
    a03 = oracle_a03_terminal_outcomes(
        rows,
        start=datetime.fromisoformat(cast(str, first.dimensions["window_start"])),
        end=datetime.fromisoformat(cast(str, first.dimensions["window_end"])),
    )
    if not isinstance(a03, A03TerminalOutcomeResult):
        return None
    return {
        "a03": {
            "succeeded_count": a03.succeeded_count,
            "failed_count": a03.failed_count,
            "no_data_count": a03.no_data_count,
            "observed_count": a03.observed_count,
            "unclassified_terminal_state_count": a03.unclassified_terminal_state_count,
        },
        "a04": {
            "policy_identity": policy.policy_identity,
            "interval_seconds": int(policy.interval.total_seconds()),
            "timezone": policy.timezone,
            "anchor_time": policy.anchor_time.astimezone(UTC).isoformat(),
            "latest_successful_completion": max(
                (
                    row.source_time.astimezone(UTC).isoformat()
                    for row in rows
                    if row.terminal_state == "succeeded"
                ),
                default=None,
            ),
            "policy_identity_count": 1,
            "interval_seconds_count": 1,
            "timezone_count": 1,
            "anchor_time_count": 1,
        },
    }


def _outbox_local(item: LiveExperimentEvidence) -> dict[str, object] | None:
    population = _outbox_population(item)
    if population is None:
        return None
    events, attempts, authority = population
    reduction = e5_local_oracle(events=events, attempts=attempts, population=authority)
    return None if reduction is None else _outbox_plain(reduction)


def _typed_equal(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, Mapping):
        return (
            isinstance(actual, Mapping)
            and set(actual) == set(expected)
            and all(_typed_equal(actual[key], value) for key, value in expected.items())
        )
    return actual == expected


def _deep_plain_cohort_oracle(
    oracle: Mapping[str, Mapping[str, int | float]],
) -> dict[str, dict[str, int | float]]:
    return {cohort: dict(sorted(metrics.items())) for cohort, metrics in sorted(oracle.items())}


def _ledger_local(
    item: LiveExperimentEvidence, events: Sequence[DerivedEvent], window: ExtractionWindow
) -> dict[str, object] | None:
    source = tuple(row for row in item.primitives if isinstance(row, LedgerMaintenancePrimitive))
    event_ids = tuple(
        event.event_id
        for event in events
        if event.attributes["dashboard.experiment_id"] == "E-Pipeline-6"
    )
    if len(source) != len(event_ids):
        return None
    observations = tuple(
        LedgerRemoteResult(
            event_id,
            row.database_identity,
            row.check_type,
            row.completed_at,
            row.result,
            row.backup_completed_at,
            row.database_bytes,
            row.wal_bytes,
            row.freelist_count,
            row.page_count,
            row.migration_state,
            row.runtime_host,
        )
        for row, event_id in zip(source, event_ids, strict=True)
    )
    authority = LedgerRemotePopulationAuthority(event_ids, window.start, window.end)
    oracle = reconcile_ledger_remote(authority, observations)
    return None if oracle.selected is None else _ledger_plain(oracle.selected)


def _local_item_oracle(
    item: LiveExperimentEvidence,
    primitives: Sequence[DerivedEvent],
    window: ExtractionWindow,
) -> Mapping[str, object] | None:
    value: Mapping[str, object] | None = None
    match item.proof.experiment_id.value:
        case "E-Pipeline-1" if item.primitives:
            value = _snapshot_local(item)
        case "E-Pipeline-2" if item.primitives:
            value = _cadence_local(item)
        case "E-Pipeline-3" if item.proof.result is ExperimentResult.PROVEN:
            value = _deep_plain_cohort_oracle(item.remote_oracle)
        case "E-Pipeline-4" if item.primitives:
            value = _integrity_oracle(item.proof, item)
        case "E-Pipeline-5":
            value = _outbox_local(item)
        case "E-Pipeline-6":
            value = _ledger_local(item, primitives, window)
    return value


def _local_oracle(
    evidence: Sequence[LiveExperimentEvidence],
    primitives: Sequence[DerivedEvent],
    window: ExtractionWindow,
) -> dict[str, Mapping[str, object]]:
    oracle: dict[str, Mapping[str, object]] = {}
    for item in evidence:
        if (value := _local_item_oracle(item, primitives, window)) is not None:
            oracle[item.proof.experiment_id.value] = value
    return oracle


@dataclass(frozen=True, slots=True)
class _AuthorityInputs:
    evidence: Sequence[LiveExperimentEvidence]
    proofs: Sequence[PipelineExperimentProof]
    primitives: Sequence[DerivedEvent]
    results: Sequence[DerivedEvent]
    remote: Mapping[str, Mapping[str, object]]
    local_oracle: Mapping[str, Mapping[str, object]]


def _authority(inputs: _AuthorityInputs) -> dict[str, PipelineExecutionAuthority]:
    evidence_by_id = {item.proof.experiment_id.value: item for item in inputs.evidence}
    primitive_ids = {
        experiment: tuple(
            event.event_id
            for event in inputs.primitives
            if event.attributes["dashboard.experiment_id"] == experiment
        )
        for experiment in EXPERIMENT_IDS
    }
    result_ids = {
        str(event.attributes["dashboard.experiment_id"]): event.event_id for event in inputs.results
    }
    bindings: dict[str, PipelineExecutionAuthority] = {}
    for proof in inputs.proofs:
        experiment = proof.experiment_id.value
        item = evidence_by_id[experiment]
        ids = primitive_ids[experiment]
        complete = proof.result is ExperimentResult.PROVEN
        bindings[experiment] = PipelineExecutionAuthority(
            proof=proof,
            primitive_event_ids=ids if complete else (),
            result_event_id=result_ids[experiment],
            remote_query_id=item.remote_query_id if complete else None,
            local_oracle=inputs.local_oracle.get(experiment, {}) if complete else {},
            remote_result=dict(inputs.remote[experiment]) if complete else {},
            delivery={
                "primitive_selected": len(ids),
                "primitive_delivered": len(ids),
                "result_selected": 1,
                "result_delivered": 1,
            },
        )
    if tuple(bindings) != EXPERIMENT_IDS:
        raise PipelineExecutionError("pipeline authority population is incomplete")
    return bindings


def run(
    *, run_id: str, start: datetime, end: datetime, output: Path, config: AppConfig | None = None
) -> RunEnvelope:
    validate_run_id(run_id)
    output = validate_output_path(output)
    window = ExtractionWindow(start, end)
    config = config or load_config()
    endpoint = validate_loopback_endpoint(config.signoz.otlp_http_endpoint)
    request = LiveProofRequest(run_id, start, end)
    connection = sqlite3.connect(f"file:{config.database.path}?mode=rw", uri=True)
    try:
        evidence = extract_live(connection, request)
        primitives = primitive_events(evidence, run_id, window)
        client = ClickHouseClient(
            docker_context=config.signoz.docker_context,
            container=config.signoz.clickhouse_container,
        )
        enqueue_events(connection, list(primitives))
        primitive_drain = _exact_drain(connection, primitives, endpoint)
        _verify_ids(client, primitives)
        remote = remote_calculations(client, primitives, window, run_id, evidence=evidence)
        local_oracle = _local_oracle(evidence, primitives, window)
        proofs = _final_proofs(evidence, remote, local_oracle)
        if any(proof.result is ExperimentResult.FAILED for proof in proofs):
            raise PipelineExecutionError("remote calculation reconciliation mismatch")
        results = result_events(proofs, run_id, window)
        enqueue_events(connection, list(results))
        result_drain = _exact_drain(connection, results, endpoint)
        _verify_ids(client, results)
        envelope = RunEnvelope(
            NAMESPACE,
            run_id,
            window,
            proofs,
            tuple(event.event_id for event in primitives),
            tuple(event.event_id for event in results),
            local_oracle,
            remote,
            {
                "primitive_selected": primitive_drain["selected"],
                "primitive_delivered": primitive_drain["delivered"],
                "result_selected": result_drain["selected"],
                "result_delivered": result_drain["delivered"],
            },
            _authority(
                _AuthorityInputs(evidence, proofs, primitives, results, remote, local_oracle)
            ),
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(envelope.canonical_json() + "\n", encoding="utf-8")
        return envelope
    finally:
        connection.close()


def _plain(value: Mapping[str, Mapping[str, object]]) -> dict[str, dict[str, object]]:
    return {key: dict(sorted(item.items())) for key, item in sorted(value.items())}


def _reject_unsafe(value: object) -> None:
    prohibited = (
        "prompt",
        "response",
        "transcript",
        "command",
        "secret",
        "credential",
        "path",
        "native_session",
        "claude",
    )
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) != "cleanup_selector" and any(
                word in str(key).lower() for word in prohibited
            ):
                raise PipelineExecutionError("unsafe evidence field")
            _reject_unsafe(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_unsafe(item)
    elif not isinstance(value, (str, int, float, bool, type(None))):
        raise PipelineExecutionError("non-JSON evidence value")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    run(
        run_id=args.run_id,
        start=datetime.fromisoformat(args.start.replace("Z", "+00:00")),
        end=datetime.fromisoformat(args.end.replace("Z", "+00:00")),
        output=Path(args.output),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Fail-closed live executor for dashboard attribution proofs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from time import sleep
from types import MappingProxyType
from typing import Protocol
from urllib.parse import urlparse

from agent_introspection.config import AppConfig, load_config
from agent_introspection.source import ClickHouseClient
from agent_introspection.telemetry import (
    DerivedEvent,
    drain_outbox_event_ids,
    enqueue_events,
    remote_event_ids,
)
from experiments.dashboard_prototype.attribution_claude_boundary import extract_claude_boundary
from experiments.dashboard_prototype.attribution_codex_app_server import (
    build_codex_app_server_proof,
    reduce_codex_app_server,
)
from experiments.dashboard_prototype.attribution_common import (
    AttributionExperimentId,
    AttributionExperimentProof,
    AttributionRowEvidence,
    AttributionRowState,
)
from experiments.dashboard_prototype.attribution_live_baseline import AttributionRowAuthority
from experiments.dashboard_prototype.attribution_live_baseline import (
    extract as extract_baseline,
)
from experiments.dashboard_prototype.attribution_live_baseline import (
    row_evidence_inputs as baseline_row_evidence_inputs,
)
from experiments.dashboard_prototype.attribution_live_common import (
    AttributionLiveEvidence,
    LiveProofRequest,
)
from experiments.dashboard_prototype.attribution_live_late_context import (
    extract as extract_late_context,
)
from experiments.dashboard_prototype.attribution_live_lifecycle_delay import (
    extract as extract_lifecycle_delay,
)
from experiments.dashboard_prototype.contracts import (
    EvidenceProvenance,
    ExperimentResult,
)

NAMESPACE = "agent-introspection.dashboard-prototype.v1"
EVENT_NAME = "dashboard_prototype.attribution_snapshot.v1"
EXPERIMENT_IDS = tuple(f"E-Attribution-{n}" for n in range(1, 6))
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z", re.ASCII)
FIXTURE = Path("tests/fixtures/producer_identity_proofs.json")

# Each calculation reconciles its uncapped event-ID population in the same bounded
# run/family/window query as the scalar result; no mutable second membership query.
_E1_SQL = """
SELECT attributes_string['dashboard.primitive_kind'] AS primitive_kind,
 attributes_string['dashboard.producer'] AS producer,
 attributes_string['dashboard.surface'] AS surface,
 attributes_string['dashboard.direction'] AS direction,
 attributes_string['dashboard.matched'] AS matched,
 attributes_string['dashboard.outcome'] AS outcome,
 attributes_string['dashboard.method'] AS method,
 attributes_string['dashboard.diagnostic'] AS diagnostic,
 attributes_string['dashboard.project_digest'] AS project_digest,
 toUInt32(uniqExact(attributes_string['event.id'])) AS count,
 groupUniqArray(attributes_string['event.id']) AS selected_event_ids,
 toUInt32(uniqExact((attributes_string['event.id'], timestamp))) AS immutable_payload_count
FROM signoz_logs.distributed_logs_v2
WHERE timestamp > {start_ns:UInt64} AND timestamp <= {end_ns:UInt64}
 AND ts_bucket_start BETWEEN {start_bucket:UInt64} AND {end_bucket:UInt64}
 AND resource.`service.name`::String = 'agent-introspection'
 AND attributes_string['event.name'] = {event_name:String}
 AND attributes_string['dashboard.event_kind'] = 'primitive'
 AND attributes_string['dashboard.experiment_id'] = 'E-Attribution-1'
 AND attributes_string['dashboard.run_id_hash'] = {run_id_hash:String}
 AND attributes_string['dashboard.query_id'] = {query_id:String}
GROUP BY primitive_kind, producer, surface, direction, matched, outcome, method,
 diagnostic, project_digest
ORDER BY primitive_kind, producer, surface, direction, matched, outcome, method,
 diagnostic, project_digest
""".strip()
_E2_SQL = """
SELECT attributes_string['dashboard.scenario'] AS scenario,
 toUInt32(uniqExact(attributes_string['event.id'])) AS scenario_population,
 toUInt32(uniqExactIf(attributes_string['event.id'],
   attributes_string['dashboard.accepted'] = 'true')) AS accepted_count,
 groupUniqArray(attributes_string['event.id']) AS selected_event_ids,
 toUInt32(uniqExact((attributes_string['event.id'], timestamp,
   attributes_string['dashboard.accepted']))) AS immutable_payload_count
FROM signoz_logs.distributed_logs_v2
WHERE timestamp > {start_ns:UInt64} AND timestamp <= {end_ns:UInt64}
 AND ts_bucket_start BETWEEN {start_bucket:UInt64} AND {end_bucket:UInt64}
 AND resource.`service.name`::String = 'agent-introspection'
 AND attributes_string['event.name'] = {event_name:String}
 AND attributes_string['dashboard.event_kind'] = 'primitive'
 AND attributes_string['dashboard.experiment_id'] = 'E-Attribution-2'
 AND attributes_string['dashboard.run_id_hash'] = {run_id_hash:String}
 AND attributes_string['dashboard.query_id'] = {query_id:String}
GROUP BY scenario ORDER BY scenario
""".strip()
_E4_SQL = """
SELECT cohort,
 toUInt32(count()) AS selected_sessions,
 toUInt32(countIf(matched = 'true')) AS matched_sessions,
 toUInt32(sum(negative_skew)) AS negative_skew_sessions,
 toUInt32(countIf(matched = 'true' AND negative_skew = 0)) AS n,
 if(n = 0, 0., quantileExact(0.5)(if(
   matched = 'true' AND negative_skew = 0, lifecycle_delay_seconds, NULL
 ))) AS p50_lifecycle_delay_seconds,
 if(n = 0, 0., arrayElement(arraySort(groupArrayIf(
   lifecycle_delay_seconds, matched = 'true' AND negative_skew = 0
 )), toUInt64(greatest(1, ceil(n * 0.95))))) AS p95_lifecycle_delay_seconds,
 groupUniqArray(event_id) AS selected_event_ids,
 toUInt32(count()) AS immutable_payload_count
FROM (
 SELECT DISTINCT attributes_string['event.id'] AS event_id, timestamp,
  attributes_string['dashboard.cohort'] AS cohort,
  attributes_string['dashboard.matched'] AS matched,
  attributes_number['dashboard.negative_skew'] AS negative_skew,
  attributes_number['dashboard.lifecycle_delay_seconds'] AS lifecycle_delay_seconds
 FROM signoz_logs.distributed_logs_v2
 WHERE timestamp > {start_ns:UInt64} AND timestamp <= {end_ns:UInt64}
  AND ts_bucket_start BETWEEN {start_bucket:UInt64} AND {end_bucket:UInt64}
  AND resource.`service.name`::String = 'agent-introspection'
  AND attributes_string['event.name'] = {event_name:String}
  AND attributes_string['dashboard.event_kind'] = 'primitive'
  AND attributes_string['dashboard.experiment_id'] = 'E-Attribution-4'
  AND attributes_string['dashboard.run_id_hash'] = {run_id_hash:String}
  AND attributes_string['dashboard.query_id'] = {query_id:String}
)
GROUP BY cohort ORDER BY cohort
""".strip()
_E5_SQL = """
SELECT attributes_string['dashboard.primitive_kind'] AS primitive_kind,
 attributes_string['dashboard.cohort'] AS cohort,
 attributes_string['dashboard.resolved_event_digest'] AS resolved_event_digest,
 toUInt32(uniqExactIf(attributes_string['dashboard.activity_hash'],
   attributes_string['dashboard.primitive_kind'] = 'ever_unresolved')) AS ever_unresolved_count,
 toUInt32(arraySum(arrayMap(pair -> pair.2, groupUniqArray((
   attributes_string['event.id'],
   attributes_number['dashboard.transition_count']
 ))))) AS transition_count,
 groupUniqArray(attributes_string['event.id']) AS selected_event_ids,
 toUInt32(uniqExact((attributes_string['event.id'], timestamp,
   attributes_string['dashboard.activity_hash'],
   attributes_number['dashboard.transition_count']))) AS immutable_payload_count
FROM signoz_logs.distributed_logs_v2
WHERE timestamp > {start_ns:UInt64} AND timestamp <= {end_ns:UInt64}
 AND ts_bucket_start BETWEEN {start_bucket:UInt64} AND {end_bucket:UInt64}
 AND resource.`service.name`::String = 'agent-introspection'
 AND attributes_string['event.name'] = {event_name:String}
 AND attributes_string['dashboard.event_kind'] = 'primitive'
 AND attributes_string['dashboard.experiment_id'] = 'E-Attribution-5'
 AND attributes_string['dashboard.run_id_hash'] = {run_id_hash:String}
 AND attributes_string['dashboard.query_id'] = {query_id:String}
GROUP BY primitive_kind, cohort, resolved_event_digest
ORDER BY primitive_kind, cohort, resolved_event_digest
""".strip()


_ROW_POPULATION_COLUMNS = """
 groupUniqArray(attributes_string['event.id']) AS selected_event_ids,
 toUInt32(uniqExact((
   attributes_string['event.id'], timestamp,
   attributes_string['dashboard.row_stage'],
   attributes_string['dashboard.row_source_member'],
   attributes_string['dashboard.row_source_with_lifecycle'],
   attributes_string['dashboard.row_lifecycle_member'],
   attributes_string['dashboard.row_lifecycle_with_source'],
   attributes_string['dashboard.row_attributed'],
   attributes_string['dashboard.row_unresolved'],
   attributes_string['dashboard.row_project_digest']
 ))) AS immutable_payload_count
""".strip()

_A07_SQL = """
SELECT {population_columns}, toUInt32(uniqExactIf(attributes_string['event.id'],
           attributes_string['dashboard.row_stage'] = 'source'
           AND attributes_string['dashboard.row_source_member'] = '1'
       )) AS source_sessions,
       toUInt32(uniqExactIf(attributes_string['event.id'],
           attributes_string['dashboard.row_stage'] = 'source'
           AND attributes_string['dashboard.row_source_with_lifecycle'] = '1'
       )) AS source_with_lifecycle,
       toUInt32(uniqExactIf(attributes_string['event.id'],
           attributes_string['dashboard.row_stage'] = 'reducer'
           AND attributes_string['dashboard.row_lifecycle_member'] = '1'
       )) AS lifecycle_sessions,
       toUInt32(uniqExactIf(attributes_string['event.id'],
           attributes_string['dashboard.row_stage'] = 'reducer'
           AND attributes_string['dashboard.row_lifecycle_with_source'] = '1'
       )) AS lifecycle_with_source
FROM signoz_logs.distributed_logs_v2
WHERE timestamp > {start_ns:UInt64} AND timestamp <= {end_ns:UInt64}
  AND attributes_string['event.id'] IN ({event_ids})
""".strip().replace("{population_columns}", _ROW_POPULATION_COLUMNS)
_A08_SQL = """
SELECT {population_columns}, toUInt32(uniqExactIf(attributes_string['event.id'],
           attributes_string['dashboard.row_stage'] = 'delivery')) AS eligible,
       toUInt32(uniqExactIf(attributes_string['event.id'],
           attributes_string['dashboard.row_stage'] = 'delivery'
           AND attributes_string['dashboard.row_attributed'] = '1'
       )) AS attributed,
       toUInt32(uniqExactIf(attributes_string['event.id'],
           attributes_string['dashboard.row_stage'] = 'delivery'
           AND attributes_string['dashboard.row_unresolved'] = '1'
       )) AS unresolved,
       toUInt32(uniqExactIf(
           attributes_string['dashboard.row_project_digest'],
           attributes_string['dashboard.row_stage'] = 'delivery'
           AND attributes_string['dashboard.row_project_digest'] != 'none'
       )) AS distinct_projects,
       groupUniqArrayIf(attributes_string['dashboard.row_project_digest'],
           attributes_string['dashboard.row_stage'] = 'delivery'
           AND attributes_string['dashboard.row_project_digest'] != 'none'
       ) AS project_digests
FROM signoz_logs.distributed_logs_v2
WHERE timestamp > {start_ns:UInt64} AND timestamp <= {end_ns:UInt64}
  AND attributes_string['event.id'] IN ({event_ids})
""".strip().replace("{population_columns}", _ROW_POPULATION_COLUMNS)
_A09_SQL = """
SELECT {population_columns}, toUInt32(uniqExactIf(attributes_string['event.id'],
           attributes_string['dashboard.row_stage'] = 'delivery')) AS eligible,
       toUInt32(uniqExactIf(attributes_string['event.id'],
           attributes_string['dashboard.row_stage'] = 'delivery'
           AND attributes_string['dashboard.row_unresolved'] = '1'
       )) AS unresolved,
       toUInt32(uniqExactIf(attributes_string['event.id'],
           attributes_string['dashboard.row_stage'] = 'delivery'
           AND attributes_string['dashboard.row_unresolved'] = '1'
       )) AS diagnostic_count
FROM signoz_logs.distributed_logs_v2
WHERE timestamp > {start_ns:UInt64} AND timestamp <= {end_ns:UInt64}
  AND attributes_string['event.id'] IN ({event_ids})
""".strip().replace("{population_columns}", _ROW_POPULATION_COLUMNS)


class AttributionExecutionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ExtractionWindow:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None or self.start >= self.end:
            raise AttributionExecutionError("window must be ordered timezone-aware instants")

    def identity(self) -> str:
        return f"{self.start.astimezone(UTC).isoformat()}..{self.end.astimezone(UTC).isoformat()}"


@dataclass(frozen=True, slots=True)
class RunEnvelope:
    namespace: str
    run_id: str
    window: ExtractionWindow
    proofs: tuple[AttributionExperimentProof, ...]
    primitive_event_ids: tuple[str, ...]
    result_event_ids: tuple[str, ...]
    local_oracle: Mapping[str, Mapping[str, object]]
    remote_result: Mapping[str, Mapping[str, object]]
    drain: Mapping[str, int]
    row_obligations: tuple[AttributionRowEvidence, ...]
    row_event_ids: tuple[str, ...]
    calculation_queries: Mapping[str, object]

    def payload(self) -> dict[str, object]:
        ids = self.primitive_event_ids + self.result_event_ids + self.row_event_ids
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
            "row_event_ids": list(self.row_event_ids),
            "calculation_queries": dict(self.calculation_queries),
            "drain": dict(self.drain),
            "row_obligations": [_row_payload(row) for row in self.row_obligations],
            "cleanup_selector": {
                "namespace": self.namespace,
                "run_id": self.run_id,
                "event_ids": list(ids),
            },
        }

    def canonical_json(self) -> str:
        return _canonical_envelope_json(self.payload())


def canonical_json(value: object) -> str:
    _reject_unsafe(value)
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _canonical_envelope_json(value: object) -> str:
    _reject_unsafe(value, top_level=True)
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def canonical_hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
        raise AttributionExecutionError("run ID is not exact and bounded")
    return run_id


def validate_output_path(path: Path) -> Path:
    root = (Path(__file__).parent / "evidence").resolve()
    candidate = path.resolve(strict=False)
    if candidate.suffix != ".json" or root not in candidate.parents:
        raise AttributionExecutionError("output must be a JSON file under prototype evidence")
    return candidate


def validate_loopback_endpoint(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise AttributionExecutionError("telemetry endpoint must be loopback HTTP")
    return endpoint.rstrip("/")


def _blocked_codex_app_server_evidence(
    request: LiveProofRequest,
) -> AttributionLiveEvidence:
    reduction = reduce_codex_app_server((), (), frozenset())
    proof = build_codex_app_server_proof(
        request.run_id,
        EvidenceProvenance.FRESH_REAL,
        request.source_boundary,
        reduction,
    )
    return AttributionLiveEvidence(proof, (), None, {})


def extract_live(
    connection: sqlite3.Connection, request: LiveProofRequest
) -> tuple[AttributionLiveEvidence, ...]:
    fixture = FIXTURE if FIXTURE.exists() else Path(__file__).parents[2] / FIXTURE
    evidence = (
        extract_baseline(connection, request, retained_fixture=fixture),
        _blocked_codex_app_server_evidence(request),
        extract_claude_boundary(fixture, request),
        extract_lifecycle_delay(connection, request),
        extract_late_context(connection, request),
    )
    if tuple(item.proof.experiment_id.value for item in evidence) != EXPERIMENT_IDS:
        raise AttributionExecutionError("typed proof ordering is incomplete")
    return evidence


@dataclass(frozen=True, slots=True)
class _EventSpec:
    kind: str
    query_id: str
    ordinal: int
    attributes: Mapping[str, str | int | float | bool]
    timestamp_ns: int | None = None


def _event(
    proof: AttributionExperimentProof,
    run_id: str,
    window: ExtractionWindow,
    spec: _EventSpec,
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
            **dict(spec.attributes),
        },
        _epoch_ns(window.end) if spec.timestamp_ns is None else spec.timestamp_ns,
    )


def _allowed_dimension_names(item: AttributionLiveEvidence) -> set[str]:
    return {
        "E-Attribution-1": {
            "producer",
            "surface",
            "outcome",
            "primitive_kind",
            "diagnostic",
            "method",
            "project_digest",
            "direction",
            "matched",
        },
        "E-Attribution-2": {"scenario", "accepted"},
        "E-Attribution-4": {"cohort", "matched"},
        "E-Attribution-5": {
            "producer",
            "surface",
            "method",
            "prior_reason",
            "primitive_kind",
            "activity_hash",
            "resolved_event_digest",
        },
    }[item.proof.experiment_id.value]


def _dimension_attributes(
    dimensions: Mapping[str, object], allowed: set[str]
) -> dict[str, str | int | float | bool]:
    attrs: dict[str, str | int | float | bool] = {}
    for key, value in dimensions.items():
        if key not in allowed:
            continue
        if isinstance(value, bool):
            attrs[f"dashboard.{key}"] = str(value).lower()
        elif isinstance(value, (str, int, float)):
            attrs[f"dashboard.{key}"] = value
        else:
            raise AttributionExecutionError("primitive dimension is not scalar")
    return attrs


def _late_context_attributes(
    dimensions: Mapping[str, object], attrs: dict[str, str | int | float | bool]
) -> None:
    kind = dimensions.get("primitive_kind")
    if kind == "ever_unresolved":
        if not isinstance(dimensions.get("activity_hash"), str):
            raise AttributionExecutionError("late-context denominator identity is missing")
        attrs["dashboard.cohort"] = "ever_unresolved"
        return
    if kind != "transition":
        raise AttributionExecutionError("late-context primitive kind is invalid")
    attrs["dashboard.cohort"] = hashlib.sha256(
        "\x1f".join(
            (
                "late-context",
                str(dimensions.get("producer", "")),
                str(dimensions.get("surface", "")),
                str(dimensions.get("method", "")),
                str(dimensions.get("prior_reason", "none")),
            )
        ).encode()
    ).hexdigest()[:16]
    digest = dimensions.get("resolved_event_digest")
    if not isinstance(digest, str) or not digest:
        raise AttributionExecutionError("late-context resolved event identity is missing")
    attrs["dashboard.resolved_event_digest"] = digest


def _measure_attributes(
    measures: Mapping[str, object], attrs: dict[str, str | int | float | bool]
) -> None:
    allowed = {
        "count",
        "lifecycle_delay_seconds",
        "negative_skew",
        "transition_count",
        "ever_unresolved_count",
    }
    for key, value in measures.items():
        if key not in allowed:
            continue
        if not isinstance(value, (str, int, float, bool)):
            raise AttributionExecutionError("primitive measure is not scalar")
        attrs[f"dashboard.{key}"] = value


def _safe_attributes(
    item: AttributionLiveEvidence, dimensions: Mapping[str, object], measures: Mapping[str, object]
) -> dict[str, str | int | float | bool]:
    attrs = _dimension_attributes(dimensions, _allowed_dimension_names(item))
    if item.proof.experiment_id.value == "E-Attribution-5":
        _late_context_attributes(dimensions, attrs)
    _measure_attributes(measures, attrs)
    _reject_unsafe(attrs)
    return attrs


def primitive_events(
    evidence: Sequence[AttributionLiveEvidence], run_id: str, window: ExtractionWindow
) -> tuple[DerivedEvent, ...]:
    events = []
    for item in evidence:
        if item.remote_query_id is None:
            continue
        for primitive in item.primitives:
            events.append(
                _event(
                    item.proof,
                    run_id,
                    window,
                    _EventSpec(
                        "primitive",
                        item.remote_query_id,
                        primitive.ordinal,
                        _safe_attributes(item, primitive.dimensions, primitive.measures),
                        _primitive_source_ns(primitive.source_time, primitive.source_time_ns),
                    ),
                )
            )
    return tuple(events)


def _primitive_source_ns(source_time: datetime, native_ns: int | None) -> int:
    represented_ns = _epoch_ns(source_time)
    if native_ns is None:
        return represented_ns
    if type(native_ns) is not int or native_ns < 0 or native_ns // 1_000 != represented_ns // 1_000:
        raise AttributionExecutionError("primitive native source time contradicts its datetime")
    return native_ns


def result_events(
    proofs: Sequence[AttributionExperimentProof], run_id: str, window: ExtractionWindow
) -> tuple[DerivedEvent, ...]:
    return tuple(
        _event(
            p,
            run_id,
            window,
            _EventSpec("result", "result-v1", i, {"dashboard.result": p.result.value}),
        )
        for i, p in enumerate(proofs, 1)
    )


class _RemoteClient(Protocol):
    def query(
        self, sql: str, parameters: Mapping[str, str | int]
    ) -> Iterable[Mapping[str, object]]: ...


def _exact_drain(
    connection: sqlite3.Connection, events: Sequence[DerivedEvent], endpoint: str
) -> Mapping[str, int]:
    if len({event.event_id for event in events}) != len(events):
        raise AttributionExecutionError("exact outbox population contains duplicate IDs")
    totals: dict[str, int] = dict.fromkeys(("selected", "delivered", "pending"), 0)
    for offset in range(0, len(events), 512):
        batch = events[offset : offset + 512]
        result = drain_outbox_event_ids(
            connection,
            [event.event_id for event in batch],
            endpoint=f"{endpoint}/v1/logs",
            include_delivered=True,
        )
        if (
            not isinstance(result, Mapping)
            or any(
                isinstance(result.get(key), bool) or not isinstance(result.get(key), int)
                for key in totals
            )
            or (result["selected"], result["delivered"], result["pending"])
            != (len(batch), len(batch), 0)
        ):
            raise AttributionExecutionError("exact outbox drain mismatch")
        for key in totals:
            totals[key] += int(result[key])
    return totals


def _verify_ids(client: _RemoteClient, events: Sequence[DerivedEvent]) -> None:
    missing = {event.event_id: event for event in events}
    for attempt in range(20):
        pending = tuple(missing.values())
        for offset in range(0, len(pending), 512):
            for event_id in remote_event_ids(client, pending[offset : offset + 512]):
                if event_id not in missing:
                    raise AttributionExecutionError("remote ID is outside the selected population")
                del missing[event_id]
        if not missing:
            return
        if attempt < 19:
            sleep(0.5)
    raise AttributionExecutionError("exact remote event IDs mismatch")


def _deliver_exact_events(
    connection: sqlite3.Connection,
    client: _RemoteClient,
    events: Sequence[DerivedEvent],
    endpoint: str,
) -> Mapping[str, int]:
    enqueue_events(connection, list(events))
    drained = _exact_drain(connection, events, endpoint)
    _verify_ids(client, events)
    return drained


def _params(window: ExtractionWindow, query_id: str, run_id: str) -> dict[str, str | int]:
    return {
        "start_ns": _epoch_ns(window.start),
        "end_ns": _epoch_ns(window.end),
        "start_bucket": max(0, _epoch_ns(window.start) // 1_000_000_000 - 1800),
        "end_bucket": _epoch_ns(window.end) // 1_000_000_000,
        "event_name": EVENT_NAME,
        "run_id_hash": canonical_hash(run_id),
        "query_id": query_id,
    }


def _number(value: object, *, integral: bool) -> int | float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or (integral and int(value) != value)
    ):
        raise AttributionExecutionError("remote calculation returned unsafe scalar")
    return int(value) if integral else float(value)


def remote_calculations(
    client: _RemoteClient,
    primitives: Sequence[DerivedEvent],
    window: ExtractionWindow,
    run_id: str,
    *,
    query_evidence: dict[str, object],
) -> dict[str, Mapping[str, object]]:
    grouped = {
        eid: tuple(
            event for event in primitives if event.attributes["dashboard.experiment_id"] == eid
        )
        for eid in EXPERIMENT_IDS
    }
    output: dict[str, Mapping[str, object]] = {}
    for eid, sql in (
        ("E-Attribution-1", _E1_SQL),
        ("E-Attribution-2", _E2_SQL),
        ("E-Attribution-4", _E4_SQL),
        ("E-Attribution-5", _E5_SQL),
    ):
        if not grouped[eid]:
            continue
        params = _params(window, str(grouped[eid][0].attributes["dashboard.query_id"]), run_id)
        record: dict[str, object] = {"sql": sql, "parameters": params, "status": "prepared"}
        query_evidence[eid] = record
        rows = list(client.query(sql, params))
        record.update({"rows": rows, "status": "returned"})
        _verify_calculation_population(rows, {event.event_id for event in grouped[eid]})
        output[eid] = _reduce_remote(eid, rows)
    return output


def _verify_calculation_population(
    rows: Sequence[Mapping[str, object]], expected: set[str]
) -> None:
    observed: set[str] = set()
    for row in rows:
        ids = row.get("selected_event_ids")
        if not isinstance(ids, list) or any(not isinstance(value, str) for value in ids):
            raise AttributionExecutionError("remote calculation lacks exact event-ID population")
        population = set(ids)
        if len(population) != len(ids) or observed.intersection(population):
            raise AttributionExecutionError("remote immutable event appears in conflicting groups")
        if _count(row, "immutable_payload_count") != len(population):
            raise AttributionExecutionError("remote immutable event payloads conflict")
        observed.update(population)
    if observed != expected:
        raise AttributionExecutionError("remote calculation event-ID population mismatch")


def _reduce_remote(eid: str, rows: Sequence[Mapping[str, object]]) -> Mapping[str, object]:
    if eid == "E-Attribution-1":
        return _reduce_e1(rows)
    if eid == "E-Attribution-2":
        return _reduce_e2(rows)
    if eid == "E-Attribution-4":
        return _reduce_cohorts(
            rows,
            (
                "selected_sessions",
                "matched_sessions",
                "negative_skew_sessions",
                "n",
                "p50_lifecycle_delay_seconds",
                "p95_lifecycle_delay_seconds",
            ),
        )
    return _reduce_e5(rows)


def _count(row: Mapping[str, object], field: str = "count") -> int:
    value = _number(row.get(field), integral=True)
    if not isinstance(value, int):
        raise AttributionExecutionError("remote count mismatch")
    return value


def _reduce_e1(rows: Sequence[Mapping[str, object]]) -> Mapping[str, object]:
    p5: dict[str, dict[str, int]] = {}
    p7rows: list[Mapping[str, object]] = []
    p8: dict[str, dict[str, int]] = {}
    for row in rows:
        count = _count(row)
        kind = row.get("primitive_kind")
        if kind == "p5":
            _add_e1_p5(p5, row, count)
        elif kind == "p7_p8":
            p7rows.extend([row] * count)
            key = "p8.{producer}.{surface}.{method}.{outcome}.{diagnostic}".format(**row)
            p8.setdefault(key, {"count": 0})["count"] += count
        else:
            raise AttributionExecutionError("remote E1 primitive mismatch")
    return {
        "p7": _p7_metrics(p7rows),
        "p8": {"total": sum(item["count"] for item in p8.values())},
        **p5,
        **_p7_cohorts(p7rows),
        **p8,
    }


def _add_e1_p5(p5: dict[str, dict[str, int]], row: Mapping[str, object], count: int) -> None:
    key = f"p5.{row['producer']}.{row['surface']}"
    metrics = p5.setdefault(
        key,
        {
            "source_sessions": 0,
            "source_with_lifecycle": 0,
            "lifecycle_sessions": 0,
            "lifecycle_with_source": 0,
        },
    )
    direction = row.get("direction")
    matched = row.get("matched") == "true"
    if direction == "source_to_lifecycle":
        metrics["source_sessions"] += count
        metrics["source_with_lifecycle"] += count if matched else 0
    elif direction == "lifecycle_to_source":
        metrics["lifecycle_sessions"] += count
        metrics["lifecycle_with_source"] += count if matched else 0
    else:
        raise AttributionExecutionError("remote E1 direction mismatch")


def _p7_metrics(rows: Sequence[Mapping[str, object]]) -> dict[str, int]:
    return {
        "eligible": len(rows),
        "attributed": sum(row.get("outcome") == "attributed" for row in rows),
        "unresolved": sum(row.get("outcome") == "unresolved" for row in rows),
        "distinct_projects": len(
            {row.get("project_digest") for row in rows if row.get("project_digest") != "none"}
        ),
    }


def _p7_cohorts(rows: Sequence[Mapping[str, object]]) -> dict[str, dict[str, int]]:
    cohorts: dict[str, dict[str, int]] = {}
    projects: dict[str, set[object]] = {}
    for row in rows:
        key = f"p7.{row['producer']}.{row['surface']}"
        metrics = cohorts.setdefault(
            key, {"eligible": 0, "attributed": 0, "unresolved": 0, "distinct_projects": 0}
        )
        outcome = row.get("outcome")
        if not isinstance(outcome, str) or outcome not in {"attributed", "unresolved"}:
            raise AttributionExecutionError("remote E1 outcome mismatch")
        metrics["eligible"] += 1
        metrics[outcome] += 1
        if row.get("project_digest") != "none":
            projects.setdefault(key, set()).add(row.get("project_digest"))
    for key, metrics in cohorts.items():
        metrics["distinct_projects"] = len(projects.get(key, set()))
    return cohorts


def _reduce_e2(rows: Sequence[Mapping[str, object]]) -> Mapping[str, object]:
    output: dict[str, dict[str, int]] = {}
    total = accepted = 0
    for row in rows:
        scenario = row.get("scenario")
        if not isinstance(scenario, str) or scenario in output:
            raise AttributionExecutionError("remote E2 scenario mismatch")
        population = _count(row, "scenario_population")
        accepted_count = _count(row, "accepted_count")
        output[scenario] = {
            "accepted_count": accepted_count,
            "scenario_population": population,
        }
        total += population
        accepted += accepted_count
    output["all"] = {
        "accepted_count": accepted,
        "required_scenarios": 4,
        "scenario_population": total,
    }
    return output


def _reduce_cohorts(
    rows: Sequence[Mapping[str, object]], keys: Sequence[str]
) -> dict[str, dict[str, int | float]]:
    output: dict[str, dict[str, int | float]] = {}
    for row in rows:
        cohort = row.get("cohort")
        if not isinstance(cohort, str) or not cohort or cohort in output:
            raise AttributionExecutionError("remote cohort mismatch")
        output[cohort] = {
            key: _number(row.get(key), integral=not key.startswith("p")) for key in keys
        }
    return output


def _reduce_e5(rows: Sequence[Mapping[str, object]]) -> Mapping[str, object]:
    output: dict[str, dict[str, object]] = {}
    for row in rows:
        kind = row.get("primitive_kind")
        cohort = row.get("cohort")
        if kind == "ever_unresolved":
            if cohort != "ever_unresolved" or cohort in output:
                raise AttributionExecutionError("remote E5 denominator identity mismatch")
            output["ever_unresolved"] = {
                "ever_unresolved_count": _count(row, "ever_unresolved_count")
            }
            continue
        digest = row.get("resolved_event_digest")
        if (
            kind != "transition"
            or not isinstance(cohort, str)
            or not cohort
            or not isinstance(digest, str)
            or not digest
        ):
            raise AttributionExecutionError("remote E5 resolved identity mismatch")
        metrics = output.setdefault(cohort, {"transition_count": 0, "resolved_event_digests": {}})
        digests = metrics["resolved_event_digests"]
        if not isinstance(digests, dict) or digest in digests:
            raise AttributionExecutionError("remote E5 resolved identity mismatch")
        count = _count(row, "transition_count")
        previous = metrics["transition_count"]
        if not isinstance(previous, int):
            raise AttributionExecutionError("remote E5 transition count mismatch")
        metrics["transition_count"] = previous + count
        digests[digest] = count
    return output


def _expected_oracle(item: AttributionLiveEvidence) -> Mapping[str, object]:
    if item.proof.experiment_id.value != "E-Attribution-5":
        return item.remote_oracle
    output: dict[str, dict[str, object]] = {}
    if "ever_unresolved" in item.remote_oracle:
        output["ever_unresolved"] = dict(item.remote_oracle["ever_unresolved"])
    for primitive in item.primitives:
        attrs = _safe_attributes(item, primitive.dimensions, primitive.measures)
        if attrs["dashboard.primitive_kind"] == "ever_unresolved":
            continue
        cohort = attrs["dashboard.cohort"]
        digest = attrs["dashboard.resolved_event_digest"]
        if not isinstance(cohort, str) or not isinstance(digest, str):
            raise AttributionExecutionError("late-context primitive identity is invalid")
        metrics = output.setdefault(cohort, {"transition_count": 0, "resolved_event_digests": {}})
        digests = metrics["resolved_event_digests"]
        if not isinstance(digests, dict) or digest in digests:
            raise AttributionExecutionError("late-context duplicate resolved identity")
        count = primitive.measures.get("transition_count")
        if not isinstance(count, int) or isinstance(count, bool):
            raise AttributionExecutionError("late-context transition count is invalid")
        previous = metrics["transition_count"]
        if not isinstance(previous, int):
            raise AttributionExecutionError("late-context transition count is invalid")
        metrics["transition_count"] = previous + count
        digests[digest] = count
    return output


def _final_proofs(
    evidence: Sequence[AttributionLiveEvidence], remote: Mapping[str, Mapping[str, object]]
) -> tuple[AttributionExperimentProof, ...]:
    proofs = []
    for item in evidence:
        proof = item.proof
        eid = proof.experiment_id.value
        if item.remote_oracle:
            expected = _deep_plain(_expected_oracle(item))
            actual = _deep_plain(remote.get(eid, {}))
            reconciled = json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True)
            if (
                reconciled
                and proof.experiment_id is AttributionExperimentId.LATE_CONTEXT
                and proof.result is ExperimentResult.BLOCKED
                and proof.blocked_boundaries == ("late-context.remote-ever-unresolved-denominator",)
                and item.primitives
            ):
                proof = replace(
                    proof,
                    result=ExperimentResult.PROVEN,
                    blocked_boundaries=(),
                    assertions={**proof.assertions, "denominator_reconciled": True},
                    metrics={
                        **proof.metrics,
                        "late_context_rate": _count(proof.metrics, "transition_count")
                        / _count(proof.metrics, "ever_unresolved_count"),
                    },
                )
            proof = replace(
                proof,
                result=ExperimentResult.FAILED if not reconciled else proof.result,
                assertions={**proof.assertions, "remote_calculation_reconciled": reconciled},
            )
        proofs.append(proof)
    return tuple(proofs)


def _deep_plain(value: Mapping[str, object]) -> dict[str, object]:
    return {str(k): _deep_value(v) for k, v in sorted(value.items())}


def _deep_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(k): _deep_value(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_deep_value(v) for v in value]
    _reject_unsafe(value)
    return value


def _plain(value: Mapping[str, Mapping[str, object]]) -> dict[str, dict[str, object]]:
    return {k: _deep_plain(v) for k, v in sorted(value.items())}


def _local_oracle(evidence: Sequence[AttributionLiveEvidence]) -> dict[str, Mapping[str, object]]:
    return {
        item.proof.experiment_id.value: (
            _deep_plain(_expected_oracle(item)) if item.remote_oracle else dict(item.proof.metrics)
        )
        for item in evidence
    }


def _row_payload(row: AttributionRowEvidence) -> dict[str, object]:
    return {
        "deterministic_id": row.deterministic_id(),
        "experiment_id": row.experiment_id.value,
        "row_id": row.row_id,
        "producer": row.producer,
        "state": row.state.value,
        "blocked_reason": row.blocked_reason,
        "members": [
            {
                "native_session_id": member.native_session_id,
                "source_id": member.source_id,
                "reducer_id": member.reducer_id,
                "event_id_inputs": member.event_id_inputs,
                "event_ids": member.event_ids,
            }
            for member in row.members or ()
        ]
        if row.members is not None
        else None,
        "direct_remote_sql_parameters": row.direct_remote_sql_parameters,
        "direct_remote_sql_parameter_types": row.direct_remote_sql_parameter_types,
        "direct_remote_result": row.direct_remote_result,
        "direct_remote_result_types": row.direct_remote_result_types,
        "oracle_parameters": row.oracle_parameters,
        "oracle_parameter_types": row.oracle_parameter_types,
        "oracle_result": row.oracle_result,
        "oracle_result_types": row.oracle_result_types,
    }


@dataclass(frozen=True, slots=True)
class _RowDirectCalculation:
    parameters: Mapping[str, str | int]
    result: Mapping[str, str | int | bool | None]

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))
        object.__setattr__(self, "result", MappingProxyType(dict(self.result)))


def row_obligations(
    evidence: Sequence[AttributionLiveEvidence],
    baseline_rows: Sequence[AttributionRowAuthority],
    delivered: Mapping[tuple[str, str, str, str, str], Mapping[str, str]] | None = None,
    direct_results: Mapping[tuple[str, str], _RowDirectCalculation] | None = None,
) -> tuple[AttributionRowEvidence, ...]:
    """Promote one full-population E1 obligation per row/producer."""
    proof_by_id = {item.proof.experiment_id: item.proof for item in evidence}
    grouped: dict[tuple[str, str], list[AttributionRowAuthority]] = {}
    for candidate in baseline_rows:
        grouped.setdefault((candidate.row_id, candidate.producer), []).append(candidate)
    rows: list[AttributionRowEvidence] = []
    aggregate = proof_by_id[AttributionExperimentId.BASELINE]
    for producer in ("omp", "codex-cli"):
        for row_id in ("A07", "A08", "A09"):
            candidates = tuple(
                sorted(
                    grouped.get((row_id, producer), ()),
                    key=lambda item: (
                        item.source_time,
                        item.native_session_id,
                        item.source_id,
                        item.reducer_id,
                    ),
                )
            )
            delivered_members = (
                tuple(
                    (candidate, delivered.get(_row_member_key(candidate)))
                    for candidate in candidates
                )
                if delivered
                else ()
            )
            if not candidates:
                reason = (
                    "missing_canonical_activity_authority"
                    if row_id in {"A08", "A09"}
                    else "missing_row_native_authority"
                )
                rows.append(
                    _blocked_row(AttributionExperimentId.BASELINE, row_id, producer, reason)
                )
            elif any(not candidate.native_identity_bound for candidate in candidates):
                rows.append(
                    _blocked_row(
                        AttributionExperimentId.BASELINE,
                        row_id,
                        producer,
                        "missing_row_native_authority",
                    )
                )
            elif (
                aggregate.provenance is not EvidenceProvenance.FRESH_REAL
                or len(delivered_members) != len(candidates)
                or any(ids is None for _, ids in delivered_members)
            ):
                rows.append(
                    _blocked_row(
                        AttributionExperimentId.BASELINE,
                        row_id,
                        producer,
                        "undelivered_row_authority",
                    )
                )
            else:
                expected = _row_population_oracle(row_id, candidates)
                query = direct_results.get((row_id, producer)) if direct_results else None
                actual = query.result if query is not None else None
                if (
                    query is None
                    or actual is None
                    or actual != expected
                    or {key: _row_scalar_type(value) for key, value in actual.items()}
                    != {key: _row_scalar_type(value) for key, value in expected.items()}
                ):
                    rows.append(
                        _blocked_row(
                            AttributionExperimentId.BASELINE,
                            row_id,
                            producer,
                            "row_calculation_mismatch",
                        )
                    )
                else:
                    rows.append(_ready_row(row_id, producer, delivered_members, expected, query))
    rows.extend(_app_server_rows(proof_by_id[AttributionExperimentId.CODEX_APP_SERVER], evidence))
    return tuple(rows)


def _row_member_key(row: AttributionRowAuthority) -> tuple[str, str, str, str, str]:
    return (row.row_id, row.producer, row.native_session_id, row.source_id, row.reducer_id)


def _row_population_oracle(
    row_id: str, candidates: Sequence[AttributionRowAuthority]
) -> dict[str, str | int | bool | None]:
    if row_id == "A07":
        keys = (
            "source_sessions",
            "source_with_lifecycle",
            "lifecycle_sessions",
            "lifecycle_with_source",
        )
        return {key: sum(_member_count(row, key) for row in candidates) for key in keys}
    if row_id == "A08":
        return {
            "eligible": len(candidates),
            "attributed": sum(_member_count(row, "attributed") for row in candidates),
            "unresolved": sum(_member_count(row, "unresolved") for row in candidates),
            "distinct_projects": len(
                {
                    row.expected_result["project_digest"]
                    for row in candidates
                    if row.expected_result["project_digest"] != "none"
                }
            ),
        }
    return {
        "eligible": len(candidates),
        "unresolved": sum(_member_count(row, "unresolved") for row in candidates),
        "diagnostic_count": sum(_member_count(row, "diagnostic_count") for row in candidates),
    }


def _member_count(row: AttributionRowAuthority, key: str) -> int:
    value = row.expected_result.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise AttributionExecutionError("row authority contains a non-integer calculation")
    return value


def _ready_row(
    row_id: str,
    producer: str,
    delivered_members: Sequence[tuple[AttributionRowAuthority, Mapping[str, str] | None]],
    result: Mapping[str, str | int | bool | None],
    query: _RowDirectCalculation,
) -> AttributionRowEvidence:
    from experiments.dashboard_prototype.attribution_common import AttributionRowMemberEvidence

    direct_result = query.result

    members = []
    for candidate, event_ids in delivered_members:
        assert event_ids is not None
        inputs = {
            stage: {
                "experiment_id": AttributionExperimentId.BASELINE.value,
                "row_id": row_id,
                "producer": producer,
                "native_session_id": candidate.native_session_id,
                "source_id": candidate.source_id,
                "reducer_id": candidate.reducer_id,
                "entity_version": 3,
                "event_id_ordinal": ordinal,
            }
            for ordinal, stage in enumerate(("source", "reducer", "delivery"))
        }
        members.append(
            AttributionRowMemberEvidence(
                candidate.native_session_id,
                candidate.source_id,
                candidate.reducer_id,
                inputs,
                dict(event_ids),
            )
        )
    parameters = dict(query.parameters)
    types = {key: _row_scalar_type(value) for key, value in parameters.items()}
    result_types = {key: _row_scalar_type(value) for key, value in result.items()}
    direct_types = {key: _row_scalar_type(value) for key, value in direct_result.items()}
    oracle_parameters: dict[str, str | int] = {
        "start_ns": int(parameters["start_ns"]),
        "end_ns": int(parameters["end_ns"]),
        "members_json": json.dumps(
            [
                {
                    "native_session_id": candidate.native_session_id,
                    "source_id": candidate.source_id,
                    "reducer_id": candidate.reducer_id,
                    "source_time_ns": str(candidate.source_time_ns),
                    "measures": dict(candidate.expected_result),
                }
                for candidate, _ in delivered_members
            ],
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
    }
    oracle_types = {key: _row_scalar_type(value) for key, value in oracle_parameters.items()}
    return AttributionRowEvidence(
        AttributionExperimentId.BASELINE,
        row_id,
        producer,
        AttributionRowState.READY,
        None,
        tuple(members),
        parameters,
        types,
        dict(direct_result),
        direct_types,
        oracle_parameters,
        oracle_types,
        dict(result),
        dict(result_types),
    )


def _row_scalar_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, str):
        return "string"
    raise AttributionExecutionError("row value is not scalar")


def row_primitive_events(
    rows: Sequence[AttributionRowAuthority], run_id: str, window: ExtractionWindow
) -> tuple[DerivedEvent, ...]:
    """Emit an immutable source/reducer/delivery triplet for every exact member."""
    del run_id, window
    events: list[DerivedEvent] = []
    for row in rows:
        entity_id = hashlib.sha256(
            "\x1f".join(
                (
                    AttributionExperimentId.BASELINE.value,
                    row.row_id,
                    row.producer,
                    row.native_session_id,
                    row.source_id,
                    row.reducer_id,
                )
            ).encode()
        ).hexdigest()
        for ordinal, stage in enumerate(("source", "reducer", "delivery")):
            events.append(
                DerivedEvent(
                    NAMESPACE,
                    entity_id,
                    3,
                    ordinal,
                    "dashboard_prototype.attribution_row.v1",
                    {
                        "dashboard.row_id": row.row_id,
                        "dashboard.row_stage": stage,
                        "dashboard.producer": row.producer,
                        "dashboard.source_id": canonical_hash(row.source_id),
                        "dashboard.reducer_id": canonical_hash(row.reducer_id),
                        "dashboard.row_source_member": str(
                            row.expected_result.get("source_sessions", 0)
                        ),
                        "dashboard.row_lifecycle_member": str(
                            row.expected_result.get("lifecycle_sessions", 0)
                        ),
                        "dashboard.row_source_with_lifecycle": str(
                            row.expected_result.get("source_with_lifecycle", 0)
                        ),
                        "dashboard.row_lifecycle_with_source": str(
                            row.expected_result.get("lifecycle_with_source", 0)
                        ),
                        "dashboard.row_attributed": str(row.expected_result.get("attributed", 0)),
                        "dashboard.row_unresolved": str(row.expected_result.get("unresolved", 0)),
                        "dashboard.row_project_digest": str(
                            row.expected_result.get("project_digest", "none")
                        ),
                    },
                    row.source_time_ns,
                )
            )
    return tuple(events)


def delivered_row_ids(
    rows: Sequence[AttributionRowAuthority], events: Sequence[DerivedEvent]
) -> Mapping[tuple[str, str, str, str, str], Mapping[str, str]]:
    """Bind every exact derived member ID which reached remote storage."""
    if len(events) != len(rows) * 3:
        raise AttributionExecutionError("row event population is incomplete")
    return {
        _row_member_key(row): {
            stage: events[index * 3 + ordinal].event_id
            for ordinal, stage in enumerate(("source", "reducer", "delivery"))
        }
        for index, row in enumerate(rows)
    }


def _group_row_event_ids(
    rows: Sequence[AttributionRowAuthority],
    event_ids: Mapping[tuple[str, str, str, str, str], Mapping[str, str]],
) -> dict[tuple[str, str], list[str]]:
    grouped: dict[tuple[str, str], list[str]] = {}
    for row in rows:
        grouped.setdefault((row.row_id, row.producer), []).extend(
            event_ids[_row_member_key(row)].values()
        )
    return grouped


def _direct_row_result(
    client: _RemoteClient,
    sql: str,
    parameters: Mapping[str, str | int],
    batch_context: tuple[int, Sequence[str]],
    batches: list[Mapping[str, object]],
) -> tuple[Mapping[str, object], Mapping[str, str | int]]:
    offset, batch = batch_context
    bindings = {
        "start_ns": parameters["start_ns"],
        "end_ns": parameters["end_ns"],
        **{f"event_{offset + index}": event_id for index, event_id in enumerate(batch)},
    }
    placeholders = ", ".join(f"{{event_{offset + index}:String}}" for index in range(len(batch)))
    query = sql.replace("{event_ids}", placeholders)
    record: dict[str, object] = {
        "sql": query,
        "parameters": bindings,
        "status": "prepared",
    }
    batches.append(record)
    result = list(client.query(query, bindings))
    record.update({"rows": result, "status": "returned"})
    if len(result) != 1:
        raise AttributionExecutionError("row direct SQL must return one scalar row")
    _verify_calculation_population(result, set(batch))
    return result[0], bindings


def _add_direct_row_totals(
    totals: dict[str, str | int | bool | None], result: Mapping[str, object]
) -> None:
    excluded = {
        "selected_event_ids",
        "immutable_payload_count",
        "project_digests",
        "distinct_projects",
    }
    for key in result:
        if key not in excluded:
            totals[key] = (
                _count(totals, key) + _count(result, key) if key in totals else _count(result, key)
            )


def _direct_row_projects(result: Mapping[str, object]) -> set[str]:
    project_ids = result.get("project_digests")
    if not isinstance(project_ids, list) or any(
        not isinstance(value, str) or not value or value == "none" for value in project_ids
    ):
        raise AttributionExecutionError("remote row lacks distinct project identities")
    if len(set(project_ids)) != _count(result, "distinct_projects"):
        raise AttributionExecutionError("remote distinct project population mismatch")
    return set(project_ids)


def direct_row_calculations(
    client: _RemoteClient,
    rows: Sequence[AttributionRowAuthority],
    event_ids: Mapping[tuple[str, str, str, str, str], Mapping[str, str]],
    window: ExtractionWindow,
    *,
    query_evidence: dict[str, object],
) -> dict[tuple[str, str], _RowDirectCalculation]:
    """Merge disjoint row counts; union project identities, never distinct counts."""
    output: dict[tuple[str, str], _RowDirectCalculation] = {}
    for (row_id, producer), ids in _group_row_event_ids(rows, event_ids).items():
        if len(set(ids)) != len(ids):
            raise AttributionExecutionError("row population contains duplicate immutable IDs")
        sql = _A07_SQL if row_id == "A07" else _A08_SQL if row_id == "A08" else _A09_SQL
        parameters: dict[str, str | int] = {
            "start_ns": _epoch_ns(window.start),
            "end_ns": _epoch_ns(window.end),
        }
        totals: dict[str, str | int | bool | None] = {}
        projects: set[str] = set()
        batches: list[Mapping[str, object]] = []
        query_evidence[f"{row_id}.{producer}"] = {"batches": batches, "result": totals}
        for offset in range(0, len(ids), 512):
            result, bindings = _direct_row_result(
                client, sql, parameters, (offset, ids[offset : offset + 512]), batches
            )
            _add_direct_row_totals(totals, result)
            if row_id == "A08":
                projects.update(_direct_row_projects(result))
            parameters.update(bindings)
        if row_id == "A08":
            totals["distinct_projects"] = len(projects)
        output[(row_id, producer)] = _RowDirectCalculation(parameters, totals)
    return output


def _row_value(value: object) -> str | int | bool | None:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise AttributionExecutionError("row direct SQL returned an untyped scalar")


def _blocked_row(
    experiment_id: AttributionExperimentId, row_id: str, producer: str, reason: str
) -> AttributionRowEvidence:
    return AttributionRowEvidence(
        experiment_id,
        row_id,
        producer,
        AttributionRowState.BLOCKED,
        reason,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )


def _app_server_rows(
    aggregate: AttributionExperimentProof, evidence: Sequence[AttributionLiveEvidence]
) -> list[AttributionRowEvidence]:
    """Retain E2 aggregate evidence without promoting unexecuted row obligations."""
    del aggregate, evidence
    return [
        _blocked_row(
            AttributionExperimentId.CODEX_APP_SERVER,
            row_id,
            "codex-app-server",
            "no_row_authority",
        )
        for row_id in ("A07", "A08", "A09")
    ]


def _epoch_ns(value: datetime) -> int:
    delta = value.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


def run(
    *, run_id: str, start: datetime, end: datetime, output: Path, config: AppConfig | None = None
) -> RunEnvelope:
    validate_run_id(run_id)
    output = validate_output_path(output)
    if output.exists():
        raise AttributionExecutionError("immutable evidence output already exists")
    window = ExtractionWindow(start, end)
    config = config or load_config()
    endpoint = validate_loopback_endpoint(config.signoz.otlp_http_endpoint)
    request = LiveProofRequest(run_id, start, end)
    connection = sqlite3.connect(f"file:{config.database.path}?mode=rw", uri=True)
    evidence: tuple[AttributionLiveEvidence, ...] = ()
    primitives: tuple[DerivedEvent, ...] = ()
    row_events: tuple[DerivedEvent, ...] = ()
    results: tuple[DerivedEvent, ...] = ()
    calculation_queries: dict[str, object] = {}
    stage = "source_extraction"
    try:
        connection.execute("BEGIN")
        evidence = extract_live(connection, request)
        baseline_rows = baseline_row_evidence_inputs(connection, request)
        connection.commit()
        primitives = primitive_events(evidence, run_id, window)
        row_events = row_primitive_events(baseline_rows, run_id, window)
        client = ClickHouseClient(
            docker_context=config.signoz.docker_context,
            container=config.signoz.clickhouse_container,
        )
        stage = "primitive_delivery"
        pd = _deliver_exact_events(connection, client, primitives, endpoint)
        stage = "aggregate_calculation"
        remote = remote_calculations(
            client,
            primitives,
            window,
            run_id,
            query_evidence=calculation_queries,
        )
        proofs = _final_proofs(evidence, remote)
        if any(proof.result is ExperimentResult.FAILED for proof in proofs):
            raise AttributionExecutionError("remote calculation reconciliation mismatch")
        stage = "result_delivery"
        results = result_events(proofs, run_id, window)
        rd = _deliver_exact_events(connection, client, results, endpoint)
        stage = "row_delivery"
        row_drain = _deliver_exact_events(connection, client, row_events, endpoint)
        row_ids = delivered_row_ids(baseline_rows, row_events)
        stage = "row_calculation"
        direct_rows = direct_row_calculations(
            client,
            baseline_rows,
            row_ids,
            window,
            query_evidence=calculation_queries,
        )
        obligations = row_obligations(
            tuple(replace(item, proof=proof) for item, proof in zip(evidence, proofs, strict=True)),
            baseline_rows,
            row_ids,
            direct_rows,
        )
        envelope = RunEnvelope(
            NAMESPACE,
            run_id,
            window,
            proofs,
            tuple(event.event_id for event in primitives),
            tuple(event.event_id for event in results),
            _local_oracle(evidence),
            remote,
            {
                "primitive_selected": pd["selected"],
                "primitive_delivered": pd["delivered"],
                "result_selected": rd["selected"],
                "result_delivered": rd["delivered"],
                "row_selected": row_drain["selected"],
                "row_delivered": row_drain["delivered"],
            },
            obligations,
            tuple(event.event_id for event in row_events),
            calculation_queries,
        )
        stage = "evidence_write"
        _write_evidence(output, envelope.canonical_json())
        return envelope
    except BaseException as error:
        if not output.exists():
            _write_evidence(
                output,
                _canonical_envelope_json(
                    {
                        "namespace": NAMESPACE,
                        "run_id": run_id,
                        "status": "failed",
                        "recorded_at": datetime.now(UTC).isoformat(),
                        "failed_stage": stage,
                        "error_type": type(error).__name__,
                        "window": {"start": start.isoformat(), "end": end.isoformat()},
                        "event_map": {
                            "primitive": [event.event_id for event in primitives],
                            "row": [event.event_id for event in row_events],
                            "result": [event.event_id for event in results],
                        },
                        "domain_local_oracle": _local_oracle(evidence),
                        "calculation_queries": calculation_queries,
                    }
                ),
            )
        raise
    finally:
        connection.close()


def _write_evidence(output: Path, payload: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        handle.write(payload + "\n")


def _reject_unsafe(value: object, *, top_level: bool = False) -> None:
    prohibited = (
        "prompt",
        "response",
        "transcript",
        "command",
        "secret",
        "credential",
        "path",
        "native_session",
        "session_id",
    )
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized == "row_obligations":
                if not top_level:
                    raise AttributionExecutionError("row obligations must be top-level")
                _reject_row_obligations(item)
                continue
            if normalized != "cleanup_selector" and any(word in normalized for word in prohibited):
                raise AttributionExecutionError("unsafe evidence field")
            _reject_unsafe(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_unsafe(item)
    elif isinstance(value, float) and not math.isfinite(value):
        raise AttributionExecutionError("unsafe scalar")
    elif not isinstance(value, (str, int, float, bool, type(None))):
        raise AttributionExecutionError("non-JSON evidence value")


_ROW_PAYLOAD_FIELDS = frozenset(
    (
        "deterministic_id",
        "experiment_id",
        "row_id",
        "producer",
        "state",
        "blocked_reason",
        "members",
        "direct_remote_sql_parameters",
        "direct_remote_sql_parameter_types",
        "direct_remote_result",
        "direct_remote_result_types",
        "oracle_parameters",
        "oracle_parameter_types",
        "oracle_result",
        "oracle_result_types",
    )
)


def _reject_row_obligations(value: object) -> None:
    if not isinstance(value, list):
        raise AttributionExecutionError("row obligations must be a list")
    for raw_row in value:
        row = _canonical_row_evidence(raw_row)
        if _row_payload(row) != dict(raw_row):
            raise AttributionExecutionError("row obligation must use canonical evidence fields")


def _canonical_row_evidence(value: object) -> AttributionRowEvidence:
    from experiments.dashboard_prototype.attribution_common import AttributionRowMemberEvidence

    if not isinstance(value, Mapping) or {str(key) for key in value} != _ROW_PAYLOAD_FIELDS:
        raise AttributionExecutionError("row obligation must have the complete canonical schema")
    if any(not isinstance(key, str) for key in value):
        raise AttributionExecutionError("row obligation field names must be exact strings")
    raw_members = value["members"]
    try:
        members = (
            None
            if raw_members is None
            else tuple(
                AttributionRowMemberEvidence(
                    item["native_session_id"],
                    item["source_id"],
                    item["reducer_id"],
                    item["event_id_inputs"],
                    item["event_ids"],
                )
                for item in raw_members
            )
        )
        row = AttributionRowEvidence(
            AttributionExperimentId(value["experiment_id"]),
            value["row_id"],
            value["producer"],
            AttributionRowState(value["state"]),
            value["blocked_reason"],
            members,
            value["direct_remote_sql_parameters"],
            value["direct_remote_sql_parameter_types"],
            value["direct_remote_result"],
            value["direct_remote_result_types"],
            value["oracle_parameters"],
            value["oracle_parameter_types"],
            value["oracle_result"],
            value["oracle_result_types"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise AttributionExecutionError("invalid canonical row obligation") from error
    if value["deterministic_id"] != row.deterministic_id():
        raise AttributionExecutionError("row obligation deterministic ID is invalid")
    for field in _ROW_PAYLOAD_FIELDS - {"members"}:
        _reject_unsafe(value[field])
    return row


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

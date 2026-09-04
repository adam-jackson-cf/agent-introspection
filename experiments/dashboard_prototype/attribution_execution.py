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

# Each query is bounded by the event family, temporal boundary, hashed run, query identity,
# and exact emitted IDs.  These are intentionally scalar-only telemetry projections.
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
 toUInt32(count()) AS count
FROM signoz_logs.distributed_logs_v2
WHERE timestamp > {start_ns:UInt64} AND timestamp <= {end_ns:UInt64}
 AND ts_bucket_start BETWEEN {start_bucket:UInt64} AND {end_bucket:UInt64}
 AND resource.`service.name`::String = 'agent-introspection'
 AND attributes_string['event.name'] = {event_name:String}
 AND attributes_string['dashboard.event_kind'] = 'primitive'
 AND attributes_string['dashboard.experiment_id'] = 'E-Attribution-1'
 AND attributes_string['dashboard.run_id_hash'] = {run_id_hash:String}
 AND attributes_string['dashboard.query_id'] = {query_id:String}
 AND attributes_string['event.id'] IN ({event_ids})
GROUP BY primitive_kind, producer, surface, direction, matched, outcome, method,
 diagnostic, project_digest
ORDER BY primitive_kind, producer, surface, direction, matched, outcome, method,
 diagnostic, project_digest
""".strip()
_E2_SQL = """
SELECT attributes_string['dashboard.scenario'] AS scenario,
 toUInt32(count()) AS scenario_population,
 toUInt32(countIf(attributes_string['dashboard.accepted'] = 'true')) AS accepted_count
FROM signoz_logs.distributed_logs_v2
WHERE timestamp > {start_ns:UInt64} AND timestamp <= {end_ns:UInt64}
 AND ts_bucket_start BETWEEN {start_bucket:UInt64} AND {end_bucket:UInt64}
 AND resource.`service.name`::String = 'agent-introspection'
 AND attributes_string['event.name'] = {event_name:String}
 AND attributes_string['dashboard.event_kind'] = 'primitive'
 AND attributes_string['dashboard.experiment_id'] = 'E-Attribution-2'
 AND attributes_string['dashboard.run_id_hash'] = {run_id_hash:String}
 AND attributes_string['dashboard.query_id'] = {query_id:String}
 AND attributes_string['event.id'] IN ({event_ids})
GROUP BY scenario ORDER BY scenario
""".strip()
_E4_SQL = """
SELECT attributes_string['dashboard.cohort'] AS cohort,
 toUInt32(count()) AS selected_sessions,
 toUInt32(countIf(attributes_string['dashboard.matched'] = 'true')) AS matched_sessions,
 toUInt32(sum(attributes_number['dashboard.negative_skew'])) AS negative_skew_sessions,
 toUInt32(countIf(
   attributes_string['dashboard.matched'] = 'true'
   AND attributes_number['dashboard.negative_skew'] = 0
 )) AS n,
 if(n = 0, 0., quantileExact(0.5)(if(
   attributes_string['dashboard.matched'] = 'true'
   AND attributes_number['dashboard.negative_skew'] = 0,
   attributes_number['dashboard.lifecycle_delay_seconds'], NULL
 ))) AS p50_lifecycle_delay_seconds,
 if(n = 0, 0., arrayElement(arraySort(groupArrayIf(
   attributes_number['dashboard.lifecycle_delay_seconds'],
   attributes_string['dashboard.matched'] = 'true'
   AND attributes_number['dashboard.negative_skew'] = 0
 )), toUInt64(greatest(1, ceil(n * 0.95))))) AS p95_lifecycle_delay_seconds
FROM signoz_logs.distributed_logs_v2
WHERE timestamp > {start_ns:UInt64} AND timestamp <= {end_ns:UInt64}
 AND ts_bucket_start BETWEEN {start_bucket:UInt64} AND {end_bucket:UInt64}
 AND resource.`service.name` = 'agent-introspection'
 AND attributes_string['event.name'] = {event_name:String}
 AND attributes_string['dashboard.event_kind'] = 'primitive'
 AND attributes_string['dashboard.experiment_id'] = 'E-Attribution-4'
 AND attributes_string['dashboard.run_id_hash'] = {run_id_hash:String}
 AND attributes_string['dashboard.query_id'] = {query_id:String}
 AND attributes_string['event.id'] IN ({event_ids})
GROUP BY cohort ORDER BY cohort
""".strip()
_E5_SQL = """
SELECT attributes_string['dashboard.cohort'] AS cohort,
 attributes_string['dashboard.resolved_event_digest'] AS resolved_event_digest,
 toUInt32(sum(attributes_number['dashboard.transition_count'])) AS transition_count
FROM signoz_logs.distributed_logs_v2
WHERE timestamp > {start_ns:UInt64} AND timestamp <= {end_ns:UInt64}
 AND ts_bucket_start BETWEEN {start_bucket:UInt64} AND {end_bucket:UInt64}
 AND resource.`service.name` = 'agent-introspection'
 AND attributes_string['event.name'] = {event_name:String}
 AND attributes_string['dashboard.event_kind'] = 'primitive'
 AND attributes_string['dashboard.experiment_id'] = 'E-Attribution-5'
 AND attributes_string['dashboard.run_id_hash'] = {run_id_hash:String}
 AND attributes_string['dashboard.query_id'] = {query_id:String}
 AND attributes_string['event.id'] IN ({event_ids})
GROUP BY cohort, resolved_event_digest ORDER BY cohort, resolved_event_digest
""".strip()


_A07_SQL = """
SELECT toUInt32(countIf(attributes_string['dashboard.row_stage'] = 'source'))
           AS source_sessions,
       toUInt32(countIf(attributes_string['dashboard.row_stage'] = 'source'))
           AS source_with_lifecycle,
       toUInt32(countIf(attributes_string['dashboard.row_stage'] = 'reducer'))
           AS lifecycle_sessions,
       toUInt32(countIf(attributes_string['dashboard.row_stage'] = 'reducer'))
           AS lifecycle_with_source
FROM signoz_logs.distributed_logs_v2
WHERE timestamp > {start_ns:UInt64} AND timestamp <= {end_ns:UInt64}
  AND attributes_string['event.id'] IN ({event_ids})
""".strip()
_A08_SQL = """
SELECT toUInt32(countIf(attributes_string['dashboard.row_stage'] = 'delivery')) AS eligible,
       toUInt32(countIf(
           attributes_string['dashboard.row_stage'] = 'delivery'
           AND attributes_string['dashboard.row_attributed'] = '1'
       )) AS attributed,
       toUInt32(countIf(
           attributes_string['dashboard.row_stage'] = 'delivery'
           AND attributes_string['dashboard.row_unresolved'] = '1'
       )) AS unresolved,
       toUInt32(countIf(
           attributes_string['dashboard.row_stage'] = 'delivery'
           AND attributes_string['dashboard.row_project'] = '1'
       )) AS distinct_projects
FROM signoz_logs.distributed_logs_v2
WHERE timestamp > {start_ns:UInt64} AND timestamp <= {end_ns:UInt64}
  AND attributes_string['event.id'] IN ({event_ids})
""".strip()
_A09_SQL = """
SELECT toUInt32(countIf(attributes_string['dashboard.row_stage'] = 'delivery')) AS eligible,
       toUInt32(countIf(
           attributes_string['dashboard.row_stage'] = 'delivery'
           AND attributes_string['dashboard.row_unresolved'] = '1'
       )) AS unresolved,
       toUInt32(countIf(
           attributes_string['dashboard.row_stage'] = 'delivery'
           AND attributes_string['dashboard.row_unresolved'] = '1'
       )) AS diagnostic_count,
       maxIf(
           attributes_string['dashboard.row_diagnostic'],
           attributes_string['dashboard.row_stage'] = 'delivery'
       ) AS diagnostic
FROM signoz_logs.distributed_logs_v2
WHERE timestamp > {start_ns:UInt64} AND timestamp <= {end_ns:UInt64}
  AND attributes_string['event.id'] IN ({event_ids})
""".strip()


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
        int(window.end.timestamp() * 1e9) if spec.timestamp_ns is None else spec.timestamp_ns,
    )


def _safe_attributes(
    item: AttributionLiveEvidence, dimensions: Mapping[str, object], measures: Mapping[str, object]
) -> dict[str, str | int | float | bool]:
    allowed = {
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
            "resolved_event_digest",
        },
    }[item.proof.experiment_id.value]
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
    if item.proof.experiment_id.value == "E-Attribution-5":
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
    for key, value in measures.items():
        if key in {"count", "lifecycle_delay_seconds", "negative_skew", "transition_count"}:
            if not isinstance(value, (str, int, float, bool)):
                raise AttributionExecutionError("primitive measure is not scalar")
            attrs[f"dashboard.{key}"] = value
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
                        int(primitive.source_time.timestamp() * 1e9),
                    ),
                )
            )
    return tuple(events)


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
    result = drain_outbox_event_ids(
        connection,
        [e.event_id for e in events],
        endpoint=f"{endpoint}/v1/logs",
        include_delivered=True,
    )
    if (
        not isinstance(result, Mapping)
        or any(
            isinstance(result.get(k), bool) or not isinstance(result.get(k), int)
            for k in ("selected", "delivered", "pending")
        )
        or (result["selected"], result["delivered"], result["pending"])
        != (len(events), len(events), 0)
    ):
        raise AttributionExecutionError("exact outbox drain mismatch")
    return {k: int(result[k]) for k in ("selected", "delivered", "pending")}


def _verify_ids(client: _RemoteClient, events: Sequence[DerivedEvent]) -> None:
    if not events:
        return
    expected = {e.event_id for e in events}
    for attempt in range(20):
        if remote_event_ids(client, events) == expected:
            return
        if attempt < 19:
            sleep(0.5)
    raise AttributionExecutionError("exact remote event IDs mismatch")


def _params(
    events: Sequence[DerivedEvent], window: ExtractionWindow, query_id: str, run_id: str
) -> tuple[str, dict[str, str | int]]:
    placeholders = ", ".join(f"{{event_{i}:String}}" for i in range(len(events)))
    params: dict[str, str | int] = {
        "start_ns": int(window.start.timestamp() * 1e9),
        "end_ns": int(window.end.timestamp() * 1e9),
        "start_bucket": max(0, int(window.start.timestamp()) - 1800),
        "end_bucket": int(window.end.timestamp()),
        "event_name": EVENT_NAME,
        "run_id_hash": canonical_hash(run_id),
        "query_id": query_id,
    }
    params.update({f"event_{i}": e.event_id for i, e in enumerate(events)})
    return placeholders, params


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
    client: _RemoteClient, primitives: Sequence[DerivedEvent], window: ExtractionWindow, run_id: str
) -> dict[str, Mapping[str, object]]:
    grouped = {
        eid: tuple(e for e in primitives if e.attributes["dashboard.experiment_id"] == eid)
        for eid in EXPERIMENT_IDS
    }
    output = {}
    for eid, sql in (
        ("E-Attribution-1", _E1_SQL),
        ("E-Attribution-2", _E2_SQL),
        ("E-Attribution-4", _E4_SQL),
        ("E-Attribution-5", _E5_SQL),
    ):
        if not grouped[eid]:
            continue
        placeholders, params = _params(
            grouped[eid], window, str(grouped[eid][0].attributes["dashboard.query_id"]), run_id
        )
        rows = list(client.query(sql.replace("{event_ids}", placeholders), params))
        output[eid] = _reduce_remote(eid, rows)
    return output


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
        cohort = row.get("cohort")
        digest = row.get("resolved_event_digest")
        if not isinstance(cohort, str) or not cohort or not isinstance(digest, str) or not digest:
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
    for primitive in item.primitives:
        attrs = _safe_attributes(item, primitive.dimensions, primitive.measures)
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
            reconciled = actual == expected
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
        "native_session_id": row.native_session_id,
        "state": row.state.value,
        "blocked_reason": row.blocked_reason,
        "event_id_inputs": row.event_id_inputs,
        "event_ids": row.event_ids,
        "direct_remote_sql_parameters": row.direct_remote_sql_parameters,
        "direct_remote_sql_parameter_types": row.direct_remote_sql_parameter_types,
        "direct_remote_result": row.direct_remote_result,
        "direct_remote_result_types": row.direct_remote_result_types,
        "oracle_parameters": row.oracle_parameters,
        "oracle_parameter_types": row.oracle_parameter_types,
        "oracle_result": row.oracle_result,
        "oracle_result_types": row.oracle_result_types,
    }


def row_obligations(
    evidence: Sequence[AttributionLiveEvidence],
    baseline_rows: Sequence[AttributionRowAuthority],
    delivered: Mapping[tuple[str, str], Mapping[str, str]] | None = None,
    direct_results: Mapping[tuple[str, str], Mapping[str, str | int | bool | None]] | None = None,
) -> tuple[AttributionRowEvidence, ...]:
    """Promote E1 rows only when post-drain derived IDs are supplied."""
    proof_by_id = {item.proof.experiment_id: item.proof for item in evidence}
    grouped: dict[tuple[str, str], list[AttributionRowAuthority]] = {}
    for candidate in baseline_rows:
        grouped.setdefault((candidate.row_id, candidate.producer), []).append(candidate)
    rows: list[AttributionRowEvidence] = []
    aggregate = proof_by_id[AttributionExperimentId.BASELINE]
    for producer in ("omp", "codex-cli"):
        for row_id in ("A07", "A08", "A09"):
            candidates = grouped.get((row_id, producer), ())
            candidate = candidates[0] if len(candidates) == 1 else None
            ids = delivered.get((row_id, producer)) if delivered else None
            if not candidates:
                reason = (
                    "missing_canonical_activity_authority"
                    if row_id in {"A08", "A09"}
                    else "missing_row_native_authority"
                )
                rows.append(
                    _blocked_row(AttributionExperimentId.BASELINE, row_id, producer, reason)
                )
            elif candidate is None:
                rows.append(
                    _blocked_row(
                        AttributionExperimentId.BASELINE,
                        row_id,
                        producer,
                        "ambiguous_row_native_authority",
                    )
                )
            elif aggregate.provenance is not EvidenceProvenance.FRESH_REAL or ids is None:
                rows.append(
                    _blocked_row(
                        AttributionExperimentId.BASELINE,
                        row_id,
                        producer,
                        "undelivered_row_authority",
                    )
                )
            elif (
                direct_results is None
                or direct_results.get((row_id, producer)) != candidate.expected_result
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
                rows.append(_ready_row(candidate, ids))
    rows.extend(_app_server_rows(proof_by_id[AttributionExperimentId.CODEX_APP_SERVER], evidence))
    return tuple(rows)


def _ready_row(
    candidate: AttributionRowAuthority, event_ids: Mapping[str, str]
) -> AttributionRowEvidence:
    inputs = {
        stage: {
            "experiment_id": AttributionExperimentId.BASELINE.value,
            "row_id": candidate.row_id,
            "producer": candidate.producer,
            "native_session_id": candidate.native_session_id,
            "source_id": candidate.source_id,
            "reducer_id": candidate.reducer_id,
            "entity_version": 2,
            "event_id_ordinal": ordinal,
        }
        for ordinal, stage in enumerate(("source", "reducer", "delivery"))
    }
    parameters: dict[str, str | int] = {
        "row_id": candidate.row_id,
        "source_id": candidate.source_id,
        "reducer_id": candidate.reducer_id,
        "event_count": len(event_ids),
    }
    result = dict(candidate.expected_result)
    types = {key: _row_scalar_type(value) for key, value in parameters.items()}
    result_types = {key: _row_scalar_type(value) for key, value in result.items()}
    return AttributionRowEvidence(
        AttributionExperimentId.BASELINE,
        candidate.row_id,
        candidate.producer,
        candidate.native_session_id,
        AttributionRowState.READY,
        None,
        inputs,
        dict(event_ids),
        parameters,
        types,
        result,
        result_types,
        dict(parameters),
        dict(types),
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
    """Emit immutable row primitives without a run-scoped payload."""
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
                    2,
                    ordinal,
                    "dashboard_prototype.attribution_row.v1",
                    {
                        "dashboard.row_id": row.row_id,
                        "dashboard.row_stage": stage,
                        "dashboard.producer": row.producer,
                        "dashboard.source_id": row.source_id,
                        "dashboard.reducer_id": row.reducer_id,
                        "dashboard.row_attributed": str(row.expected_result.get("attributed", 0)),
                        "dashboard.row_unresolved": str(row.expected_result.get("unresolved", 0)),
                        "dashboard.row_project": str(
                            row.expected_result.get("distinct_projects", 0)
                        ),
                        "dashboard.row_diagnostic": str(
                            row.expected_result.get("diagnostic") or ""
                        ),
                    },
                    int(row.source_time.timestamp() * 1e9),
                )
            )
    return tuple(events)


def delivered_row_ids(
    rows: Sequence[AttributionRowAuthority], events: Sequence[DerivedEvent]
) -> Mapping[tuple[str, str], Mapping[str, str]]:
    """Bind only the exact derived IDs which were enqueued and remotely verified."""
    expected = len(rows) * 3
    if len(events) != expected:
        raise AttributionExecutionError("row event population is incomplete")
    return {
        (row.row_id, row.producer): {
            stage: events[index * 3 + ordinal].event_id
            for ordinal, stage in enumerate(("source", "reducer", "delivery"))
        }
        for index, row in enumerate(rows)
    }


def direct_row_calculations(
    client: _RemoteClient,
    rows: Sequence[AttributionRowAuthority],
    event_ids: Mapping[tuple[str, str], Mapping[str, str]],
    window: ExtractionWindow,
) -> dict[tuple[str, str], Mapping[str, str | int | bool | None]]:
    """Execute only the row-specific A07/A08/A09 projections."""
    output: dict[tuple[str, str], Mapping[str, str | int | bool | None]] = {}
    for row in rows:
        ids = event_ids[(row.row_id, row.producer)]
        placeholders = ", ".join(f"{{event_{i}:String}}" for i in range(3))
        sql = _A07_SQL if row.row_id == "A07" else _A08_SQL if row.row_id == "A08" else _A09_SQL
        parameters: dict[str, str | int] = {
            "start_ns": int(window.start.timestamp() * 1e9),
            "end_ns": int(window.end.timestamp() * 1e9),
            **{f"event_{index}": value for index, value in enumerate(ids.values())},
        }
        result = list(client.query(sql.replace("{event_ids}", placeholders), parameters))
        if len(result) != 1:
            raise AttributionExecutionError("row direct SQL must return one scalar row")
        output[(row.row_id, row.producer)] = {
            key: _row_value(value) for key, value in result[0].items()
        }
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
        None,
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
        baseline_rows = baseline_row_evidence_inputs(connection, request)
        primitives = primitive_events(evidence, run_id, window)
        client = ClickHouseClient(
            docker_context=config.signoz.docker_context,
            container=config.signoz.clickhouse_container,
        )
        enqueue_events(connection, list(primitives))
        pd = _exact_drain(connection, primitives, endpoint)
        _verify_ids(client, primitives)
        remote = remote_calculations(client, primitives, window, run_id)
        proofs = _final_proofs(evidence, remote)
        if any(p.result is ExperimentResult.FAILED for p in proofs):
            raise AttributionExecutionError("remote calculation reconciliation mismatch")
        results = result_events(proofs, run_id, window)
        enqueue_events(connection, list(results))
        rd = _exact_drain(connection, results, endpoint)
        _verify_ids(client, results)
        row_events = row_primitive_events(baseline_rows, run_id, window)
        enqueue_events(connection, list(row_events))
        _exact_drain(connection, row_events, endpoint)
        _verify_ids(client, row_events)
        row_ids = delivered_row_ids(baseline_rows, row_events)
        direct_rows = direct_row_calculations(client, baseline_rows, row_ids, window)
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
            tuple(e.event_id for e in primitives),
            tuple(e.event_id for e in results),
            _local_oracle(evidence),
            remote,
            {
                "primitive_selected": pd["selected"],
                "primitive_delivered": pd["delivered"],
                "result_selected": rd["selected"],
                "result_delivered": rd["delivered"],
            },
            obligations,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(envelope.canonical_json() + "\n", encoding="utf-8")
        return envelope
    finally:
        connection.close()


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
        "native_session_id",
        "state",
        "blocked_reason",
        "event_id_inputs",
        "event_ids",
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
    if not isinstance(value, Mapping) or {str(key) for key in value} != _ROW_PAYLOAD_FIELDS:
        raise AttributionExecutionError("row obligation must have the complete canonical schema")
    if any(not isinstance(key, str) for key in value):
        raise AttributionExecutionError("row obligation field names must be exact strings")
    try:
        row = AttributionRowEvidence(
            AttributionExperimentId(value["experiment_id"]),
            value["row_id"],
            value["producer"],
            value["native_session_id"],
            AttributionRowState(value["state"]),
            value["blocked_reason"],
            value["event_id_inputs"],
            value["event_ids"],
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
    for field in _ROW_PAYLOAD_FIELDS - {"native_session_id", "event_id_inputs"}:
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

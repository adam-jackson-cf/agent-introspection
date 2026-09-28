"""Fail-closed live executor for disposable recurrence proofs."""

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
from experiments.dashboard_prototype.contracts import EvidenceProvenance, ExperimentResult
from experiments.dashboard_prototype.experiment_live_common import LiveProofRequest
from experiments.dashboard_prototype.recurrence_common import (
    RecurrenceExperimentId,
    RecurrenceExperimentProof,
    RecurrenceLiveEvidence,
    RecurrencePrimitive,
)

NAMESPACE = "agent-introspection.dashboard-prototype.v1"
EVENT_NAME = "dashboard_prototype.recurrence_snapshot.v1"
A38_ROW_ID = "A38"
FINDING_EXPERIMENT_ID = RecurrenceExperimentId.FINDING_PROJECTION.value
FINDING_ROW_GATE_ASSERTIONS = (
    "schema_required",
    "durable_window_bounds_authoritative",
    "membership_task_identity_authoritative",
    "selected_finding_evidence_range_exact",
    "selected_membership_denominator_reconciled",
    "latest_activity_versions_global",
)
EXPERIMENT_IDS = tuple(item.value for item in RecurrenceExperimentId)
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z", re.ASCII)


class RecurrenceExecutionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ExtractionWindow:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None or self.start >= self.end:
            raise RecurrenceExecutionError("window must be ordered timezone-aware instants")

    def identity(self) -> str:
        return f"{self.start.astimezone(UTC).isoformat()}..{self.end.astimezone(UTC).isoformat()}"


@dataclass(frozen=True, slots=True)
class RunEnvelope:
    namespace: str
    run_id: str
    window: ExtractionWindow
    proofs: tuple[RecurrenceExperimentProof, ...]
    primitive_event_ids: tuple[str, ...]
    result_event_ids: tuple[str, ...]
    local_oracle: Mapping[str, Mapping[str, Mapping[str, int | float]]]
    remote_result: Mapping[str, Mapping[str, Mapping[str, int | float]]]
    transport_query: Mapping[str, object]
    finding_evidence_bundle: Mapping[str, object] | None
    finding_capture: Mapping[str, str]
    drain: Mapping[str, int]

    def payload(self) -> dict[str, object]:
        all_ids = self.primitive_event_ids + self.result_event_ids
        return {
            "namespace": self.namespace,
            "run_id": self.run_id,
            "window": {
                "start": self.window.start.astimezone(UTC).isoformat(),
                "end": self.window.end.astimezone(UTC).isoformat(),
            },
            "proofs": [json.loads(proof.canonical_json()) for proof in self.proofs],
            "proof_hashes": {
                proof.experiment_id.value: proof.content_hash() for proof in self.proofs
            },
            "domain_local_oracle": _plain(self.local_oracle),
            "remote_result": _plain(self.remote_result),
            "provenance": EvidenceProvenance.FRESH_REAL.value
            if all(proof.provenance is EvidenceProvenance.FRESH_REAL for proof in self.proofs)
            else EvidenceProvenance.RETAINED.value,
            "event_map": {
                "primitive": list(self.primitive_event_ids),
                "result": list(self.result_event_ids),
            },
            "transport_query": dict(self.transport_query),
            "finding_evidence_bundle": _plain(self.finding_evidence_bundle)
            if self.finding_evidence_bundle is not None
            else None,
            "finding_capture": dict(self.finding_capture),
            "drain": dict(self.drain),
            "cleanup_selector": {
                "namespace": self.namespace,
                "run_id": self.run_id,
                "event_ids": list(all_ids),
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
        raise RecurrenceExecutionError("run ID is not exact and bounded")
    return run_id


def validate_output_path(path: Path) -> Path:
    root = (Path(__file__).parent / "evidence").resolve()
    candidate = path.resolve(strict=False)
    if candidate.suffix != ".json" or root not in candidate.parents:
        raise RecurrenceExecutionError("output must be a JSON file under prototype evidence")
    return candidate


def validate_loopback_endpoint(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise RecurrenceExecutionError("telemetry endpoint must be loopback HTTP")
    return endpoint.rstrip("/")


def _epoch_ns(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RecurrenceExecutionError("timestamp must be timezone-aware")
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value.astimezone(UTC) - epoch
    result = (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000
    if result < 0:
        raise RecurrenceExecutionError("window must not precede the Unix epoch")
    return result


def extract_live(
    connection: sqlite3.Connection,
    request: LiveProofRequest,
    finding_capture_path: Path,
) -> tuple[RecurrenceLiveEvidence, ...]:
    from experiments.dashboard_prototype.recurrence_live_finding import extract_capture as finding
    from experiments.dashboard_prototype.recurrence_live_intervention import extract as intervention
    from experiments.dashboard_prototype.recurrence_live_practice import extract as practice
    from experiments.dashboard_prototype.recurrence_live_rule import extract as rule
    from experiments.dashboard_prototype.recurrence_live_wave_a import extract as wave_a

    evidence = (
        wave_a(connection, request),
        finding(finding_capture_path, request),
        practice(connection, request),
        rule(connection, request),
        intervention(connection, request),
    )
    if tuple(item.proof.experiment_id.value for item in evidence) != EXPERIMENT_IDS:
        raise RecurrenceExecutionError("typed proof ordering is incomplete")
    return evidence


def _source_timestamp_ns(primitive: RecurrencePrimitive) -> int:
    if primitive.source_time_ns is not None:
        return primitive.source_time_ns
    return _epoch_ns(primitive.source_time)


def _dimension_key(primitive: RecurrencePrimitive) -> str:
    dimensions = dict(primitive.dimensions)
    return canonical_json(dimensions)


@dataclass(frozen=True, slots=True)
class _EventSpec:
    kind: str
    ordinal: int
    attributes: Mapping[str, str | int | float | bool]
    timestamp_ns: int | None = None


def _event(
    proof: RecurrenceExperimentProof,
    run_id: str,
    window: ExtractionWindow,
    spec: _EventSpec,
) -> DerivedEvent:
    entity = canonical_hash(
        {
            "namespace": NAMESPACE,
            "run_id_hash": canonical_hash(run_id),
            "experiment_id": proof.experiment_id.value,
            "proof_hash": proof.content_hash(),
            "kind": spec.kind,
            "ordinal": spec.ordinal,
            "attributes": spec.attributes,
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
            "dashboard.namespace": NAMESPACE,
            "dashboard.experiment_id": proof.experiment_id.value,
            "dashboard.run_id_hash": canonical_hash(run_id),
            "dashboard.proof_hash": proof.content_hash(),
            **dict(spec.attributes),
        },
        _epoch_ns(window.end) if spec.timestamp_ns is None else spec.timestamp_ns,
    )


def primitive_events(
    evidence: Sequence[RecurrenceLiveEvidence], run_id: str, window: ExtractionWindow
) -> tuple[DerivedEvent, ...]:
    events: list[DerivedEvent] = []
    for item in evidence:
        for primitive in item.primitives:
            measures = dict(primitive.measures)
            if not measures:
                raise RecurrenceExecutionError("recurrence primitive requires a measure")
            for value in measures.values():
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                ):
                    raise RecurrenceExecutionError("recurrence primitive measure is unsafe")
            dimension_key = _dimension_key(primitive)
            source_timestamp_ns = _source_timestamp_ns(primitive)
            events.append(
                _event(
                    item.proof,
                    run_id,
                    window,
                    _EventSpec(
                        "primitive",
                        primitive.ordinal,
                        {
                            "dashboard.dimension_json": dimension_key,
                            "dashboard.measure_json": canonical_json(measures),
                            "dashboard.source_time_ns": source_timestamp_ns,
                        },
                        source_timestamp_ns,
                    ),
                )
            )
    if len({event.event_id for event in events}) != len(events):
        raise RecurrenceExecutionError("duplicate recurrence primitive event ID")
    return tuple(events)


def result_events(
    proofs: Sequence[RecurrenceExperimentProof], run_id: str, window: ExtractionWindow
) -> tuple[DerivedEvent, ...]:
    if tuple(proof.experiment_id.value for proof in proofs) != EXPERIMENT_IDS:
        raise RecurrenceExecutionError("five typed recurrence results required")
    return tuple(
        _event(
            proof,
            run_id,
            window,
            _EventSpec("result", index, {"dashboard.result": proof.result.value}),
        )
        for index, proof in enumerate(proofs, 1)
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
        raise RecurrenceExecutionError("exact outbox drain mismatch")
    return {key: int(result[key]) for key in ("selected", "delivered", "pending")}


def _verify_ids(client: _RemoteClient, events: Sequence[DerivedEvent]) -> None:
    expected = {event.event_id for event in events}
    for _ in range(20):
        if set(remote_event_ids(client, tuple(events))) == expected:
            return
        sleep(0.5)
    raise RecurrenceExecutionError("exact remote event IDs mismatch")


def _verify_result_payloads(client: _RemoteClient, results: Sequence[DerivedEvent]) -> None:
    expected = {
        event.event_id: (
            event.attributes["dashboard.result"],
            event.attributes["dashboard.proof_hash"],
            event.attributes["dashboard.experiment_id"],
            event.attributes["dashboard.run_id_hash"],
            NAMESPACE,
            EVENT_NAME,
        )
        for event in results
    }
    parameters: dict[str, str | int] = {
        "namespace": NAMESPACE,
        "run_id_hash": str(next(iter(expected.values()))[3]),
        "event_name": EVENT_NAME,
        **{f"event_{index}": event_id for index, event_id in enumerate(expected)},
    }
    placeholders = ", ".join(f"{{event_{index}:String}}" for index in range(len(expected)))
    sql = (
        "SELECT attributes_string['event.id'] AS event_id, "
        "attributes_string['dashboard.result'] AS result, "
        "attributes_string['dashboard.proof_hash'] AS proof_hash, "
        "attributes_string['dashboard.experiment_id'] AS experiment_id, "
        "attributes_string['dashboard.run_id_hash'] AS run_id_hash, "
        "attributes_string['dashboard.namespace'] AS namespace, "
        "attributes_string['event.name'] AS event_name "
        "FROM signoz_logs.distributed_logs_v2 "
        "WHERE attributes_string['dashboard.namespace'] = {namespace:String} "
        "AND attributes_string['dashboard.run_id_hash'] = {run_id_hash:String} "
        "AND attributes_string['event.name'] = {event_name:String} "
        "AND attributes_string['event.id'] IN (" + placeholders + ")"
    )
    observed: dict[str, set[tuple[object, ...]]] = {}
    for row in client.query(sql, parameters):
        event_id = row.get("event_id")
        if not isinstance(event_id, str) or event_id not in expected:
            raise RecurrenceExecutionError("invalid remote result identity row")
        observed.setdefault(event_id, set()).add(
            (
                row.get("result"),
                row.get("proof_hash"),
                row.get("experiment_id"),
                row.get("run_id_hash"),
                row.get("namespace"),
                row.get("event_name"),
            )
        )
    if set(observed) != set(expected) or any(
        values != {expected[event_id]} for event_id, values in observed.items()
    ):
        raise RecurrenceExecutionError("remote result payload reconciliation mismatch")


def _local_oracle(
    primitives: Sequence[DerivedEvent],
) -> dict[str, dict[str, dict[str, int | float]]]:
    oracle: dict[str, dict[str, dict[str, int | float]]] = {
        experiment: {} for experiment in EXPERIMENT_IDS
    }
    for event in primitives:
        experiment = event.attributes["dashboard.experiment_id"]
        dimension = event.attributes["dashboard.dimension_json"]
        measures = json.loads(str(event.attributes["dashboard.measure_json"]))
        if (
            not isinstance(experiment, str)
            or experiment not in oracle
            or not isinstance(dimension, str)
            or not isinstance(measures, dict)
        ):
            raise RecurrenceExecutionError("invalid primitive oracle")
        target = oracle[experiment].setdefault(dimension, {})
        for measure, value in measures.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise RecurrenceExecutionError("invalid primitive oracle")
            target[measure] = target.get(measure, 0) + value
    return oracle


def remote_calculations(
    client: _RemoteClient, primitives: Sequence[DerivedEvent], window: ExtractionWindow, run_id: str
) -> tuple[dict[str, dict[str, dict[str, int | float]]], Mapping[str, object]]:
    parameters: dict[str, str | int] = {
        "start_ns": _epoch_ns(window.start),
        "end_ns": _epoch_ns(window.end),
        "run_id_hash": canonical_hash(run_id),
        "event_name": EVENT_NAME,
        "experiment_id": FINDING_EXPERIMENT_ID,
    }
    parameters.update({f"event_{index}": event.event_id for index, event in enumerate(primitives)})
    placeholders = (
        ", ".join(f"{{event_{index}:String}}" for index in range(len(primitives))) or "''"
    )
    sql = (
        "SELECT experiment_id, dimension_json, pair.1 AS measure, "
        "sum(JSONExtract(pair.2, 'Float64')) AS value, "
        "toTypeName(sum(JSONExtract(pair.2, 'Float64'))) AS value_type FROM ("
        "SELECT attributes_string['event.id'] AS event_id, "
        "any(attributes_string['dashboard.experiment_id']) AS experiment_id, "
        "any(attributes_string['dashboard.dimension_json']) AS dimension_json, "
        "any(attributes_string['dashboard.measure_json']) AS measure_json "
        "FROM signoz_logs.distributed_logs_v2 "
        "WHERE timestamp > {start_ns:UInt64} AND timestamp <= {end_ns:UInt64} "
        "AND attributes_string['event.name'] = {event_name:String} "
        "AND attributes_string['dashboard.run_id_hash'] = {run_id_hash:String} "
        "AND attributes_string['dashboard.experiment_id'] = {experiment_id:String} "
        "AND attributes_string['dashboard.event_kind'] = 'primitive' "
        "AND attributes_string['event.id'] IN (" + placeholders + ") GROUP BY event_id "
        "HAVING uniqExact(tuple(attributes_string['dashboard.experiment_id'], "
        "attributes_string['dashboard.dimension_json'], "
        "attributes_string['dashboard.measure_json'], timestamp)) = 1"
        ") ARRAY JOIN JSONExtractKeysAndValuesRaw(measure_json) AS pair "
        "WHERE match(pair.2, '^-?(0|[1-9][0-9]*)\\.[0-9]+([eE][+-]?[0-9]+)?$') "
        "GROUP BY experiment_id, dimension_json, measure"
    )
    remote: dict[str, dict[str, dict[str, int | float]]] = {
        experiment: {} for experiment in EXPERIMENT_IDS
    }
    for row in client.query(sql, parameters):
        experiment, dimension, measure, value, value_type = (
            row.get("experiment_id"),
            row.get("dimension_json"),
            row.get("measure"),
            row.get("value"),
            row.get("value_type"),
        )
        if (
            not isinstance(experiment, str)
            or experiment not in remote
            or not isinstance(dimension, str)
            or not isinstance(measure, str)
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not isinstance(value_type, str)
            or value_type != "Float64"
        ):
            raise RecurrenceExecutionError("invalid remote recurrence calculation row")
        target = remote[experiment].setdefault(dimension, {})
        if measure in target:
            raise RecurrenceExecutionError("conflicting remote duplicate tuple")
        target[measure] = float(value)
    return remote, {
        "row_id": A38_ROW_ID,
        "sql": sql,
        "parameters": parameters,
    }


def _finding_row_gate(item: RecurrenceLiveEvidence) -> bool:
    proof = item.proof
    return (
        proof.experiment_id.value == FINDING_EXPERIMENT_ID
        and proof.result is ExperimentResult.BLOCKED
        and proof.provenance is EvidenceProvenance.FRESH_REAL
        and bool(item.primitives)
        and bool(item.finding_version_source_ids)
        and bool(item.canonical_task_membership_source_ids)
        and all(
            proof.assertions.get(assertion) is True for assertion in FINDING_ROW_GATE_ASSERTIONS
        )
    )


def _typed_scalars(
    result: Mapping[str, Mapping[str, int | float]],
) -> tuple[Mapping[str, object], ...] | None:
    scalars: list[Mapping[str, object]] = []
    for dimension, measures in sorted(result.items()):
        for measure, value in sorted(measures.items()):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            declared_type = "Float64" if isinstance(value, float) else "Int64"
            if declared_type != "Float64":
                return None
            scalars.append(
                {
                    "dimension": dimension,
                    "measure": measure,
                    "value": value,
                    "declared_type": declared_type,
                }
            )
    return tuple(scalars) if scalars else None


@dataclass(frozen=True, slots=True)
class _FindingEvidenceInputs:
    evidence: Sequence[RecurrenceLiveEvidence]
    proofs: Sequence[RecurrenceExperimentProof]
    primitives: Sequence[DerivedEvent]
    results: Sequence[DerivedEvent]
    local: Mapping[str, Mapping[str, Mapping[str, int | float]]]
    remote: Mapping[str, Mapping[str, Mapping[str, int | float]]]
    transport_query: Mapping[str, object]


def _finding_evidence_bundle(inputs: _FindingEvidenceInputs) -> Mapping[str, object] | None:
    evidence = inputs.evidence
    proofs = inputs.proofs
    primitives = inputs.primitives
    results = inputs.results
    local = inputs.local
    remote = inputs.remote
    transport_query = inputs.transport_query
    item = next(
        (item for item in evidence if item.proof.experiment_id.value == FINDING_EXPERIMENT_ID),
        None,
    )
    proof = next(
        (item for item in proofs if item.experiment_id.value == FINDING_EXPERIMENT_ID),
        None,
    )
    if (
        item is None
        or proof is None
        or proof.result is not ExperimentResult.PROVEN
        or proof.provenance is not EvidenceProvenance.FRESH_REAL
        or not item.finding_version_source_ids
        or not item.canonical_task_membership_source_ids
        or not all(
            proof.assertions.get(assertion) is True for assertion in FINDING_ROW_GATE_ASSERTIONS
        )
    ):
        return None
    primitive_ids = tuple(
        event.event_id
        for event in primitives
        if event.attributes["dashboard.experiment_id"] == FINDING_EXPERIMENT_ID
    )
    result_ids = tuple(
        event.event_id
        for event in results
        if event.attributes["dashboard.experiment_id"] == FINDING_EXPERIMENT_ID
    )
    finding_local = local[FINDING_EXPERIMENT_ID]
    finding_remote = remote[FINDING_EXPERIMENT_ID]
    remote_scalars = _typed_scalars(finding_remote)
    oracle_scalars = _typed_scalars(finding_local)
    parameters = transport_query.get("parameters")
    expected_parameters = {
        "start_ns",
        "end_ns",
        "run_id_hash",
        "event_name",
        "experiment_id",
        *(f"event_{index}" for index, _ in enumerate(primitives)),
    }
    if (
        not primitive_ids
        or len(result_ids) != 1
        or finding_local != finding_remote
        or remote_scalars is None
        or oracle_scalars is None
        or remote_scalars != oracle_scalars
        or not isinstance(transport_query.get("sql"), str)
        or not isinstance(parameters, Mapping)
        or set(parameters) != expected_parameters
        or any(not isinstance(key, str) for key in parameters)
    ):
        return None
    remote_query = {
        "row_id": A38_ROW_ID,
        "sql": transport_query["sql"],
        "parameters": dict(parameters),
        "experiment_id": FINDING_EXPERIMENT_ID,
    }
    return {
        "row_id": A38_ROW_ID,
        "experiment_id": FINDING_EXPERIMENT_ID,
        "source": {
            "boundary": proof.source_boundary,
            "finding_version_ids": item.finding_version_source_ids,
            "canonical_task_membership_ids": item.canonical_task_membership_source_ids,
        },
        "reducer": {"event_ids": primitive_ids},
        "delivery": {"event_ids": result_ids},
        "remote": {
            "query_id": canonical_hash(remote_query),
            "sql": transport_query["sql"],
            "parameters": dict(parameters),
            "scalars": remote_scalars,
        },
        "oracle": {"scalars": oracle_scalars},
    }


def _final_proofs(
    evidence: Sequence[RecurrenceLiveEvidence],
    remote: Mapping[str, Mapping[str, Mapping[str, int | float]]],
    primitives: Sequence[DerivedEvent],
) -> tuple[RecurrenceExperimentProof, ...]:
    local = _local_oracle(primitives)
    if remote.get(FINDING_EXPERIMENT_ID) != local[FINDING_EXPERIMENT_ID]:
        raise RecurrenceExecutionError("A38 remote calculation reconciliation mismatch")
    finding_remote = remote[FINDING_EXPERIMENT_ID]
    finding_local = local[FINDING_EXPERIMENT_ID]
    remote_scalars = _typed_scalars(finding_remote)
    typed_reconciliation = remote_scalars is not None and remote_scalars == _typed_scalars(
        finding_local
    )
    final: list[RecurrenceExperimentProof] = []
    for item in evidence:
        proof = item.proof
        if _finding_row_gate(item) and typed_reconciliation:
            proof = replace(
                proof,
                result=ExperimentResult.PROVEN,
                blocked_boundaries=(),
                assertions={**proof.assertions, "remote_calculation_reconciled": True},
            )
        final.append(proof)
    return tuple(final)


def _plain(value: Mapping[str, object]) -> dict[str, object]:
    return {key: json.loads(canonical_json(item)) for key, item in sorted(value.items())}


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
        "session_id",
    )
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) != "cleanup_selector" and any(
                word in str(key).lower() for word in prohibited
            ):
                raise RecurrenceExecutionError("unsafe evidence field")
            _reject_unsafe(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_unsafe(item)
    elif isinstance(value, float) and not math.isfinite(value):
        raise RecurrenceExecutionError("unsafe scalar")
    elif not isinstance(value, (str, int, float, bool, type(None))):
        raise RecurrenceExecutionError("non-JSON evidence value")


def _capture_binding(path: Path) -> Mapping[str, str]:
    capture = path.resolve(strict=True)
    if capture.suffix != ".sqlite" or not capture.is_file():
        raise RecurrenceExecutionError("finding capture must be an existing SQLite file")
    digest = hashlib.sha256()
    with capture.open("rb") as source:
        for chunk in iter(lambda: source.read(1_048_576), b""):
            digest.update(chunk)
    return {"filename": capture.name, "sha256": digest.hexdigest()}


def run(
    *,
    run_id: str,
    window: ExtractionWindow,
    output: Path,
    finding_capture_path: Path,
    config: AppConfig | None = None,
) -> RunEnvelope:
    validate_run_id(run_id)
    output = validate_output_path(output)
    if output.exists():
        raise FileExistsError("recurrence output path already exists")
    finding_capture = _capture_binding(finding_capture_path)
    _epoch_ns(window.start)
    _epoch_ns(window.end)
    config = config or load_config()
    endpoint = validate_loopback_endpoint(config.signoz.otlp_http_endpoint)
    connection = sqlite3.connect(f"file:{config.database.path}?mode=rw", uri=True)
    try:
        connection.execute("BEGIN")
        evidence = extract_live(
            connection, LiveProofRequest(run_id, window.start, window.end), finding_capture_path
        )
        connection.execute("COMMIT")
        if finding_capture != _capture_binding(finding_capture_path):
            raise RecurrenceExecutionError("finding capture changed during extraction")
        primitives = primitive_events(evidence, run_id, window)
        client = ClickHouseClient(
            docker_context=config.signoz.docker_context,
            container=config.signoz.clickhouse_container,
        )
        enqueue_events(connection, list(primitives))
        primitive_drain = _exact_drain(connection, primitives, endpoint)
        _verify_ids(client, primitives)
        local_oracle = _local_oracle(primitives)
        remote, transport_query = remote_calculations(client, primitives, window, run_id)
        proofs = _final_proofs(evidence, remote, primitives)
        results = result_events(proofs, run_id, window)
        enqueue_events(connection, list(results))
        result_drain = _exact_drain(connection, results, endpoint)
        _verify_ids(client, results)
        _verify_result_payloads(client, results)
        envelope = RunEnvelope(
            NAMESPACE,
            run_id,
            window,
            proofs,
            tuple(event.event_id for event in primitives),
            tuple(event.event_id for event in results),
            local_oracle,
            remote,
            transport_query,
            _finding_evidence_bundle(
                _FindingEvidenceInputs(
                    evidence=evidence,
                    proofs=proofs,
                    primitives=primitives,
                    results=results,
                    local=local_oracle,
                    remote=remote,
                    transport_query=transport_query,
                )
            ),
            finding_capture,
            {
                "primitive_selected": primitive_drain["selected"],
                "primitive_delivered": primitive_drain["delivered"],
                "result_selected": result_drain["selected"],
                "result_delivered": result_drain["delivered"],
            },
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as destination:
            destination.write(envelope.canonical_json() + "\n")
        return envelope
    finally:
        connection.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--finding-capture-path", required=True)
    args = parser.parse_args(argv)
    run(
        run_id=args.run_id,
        window=ExtractionWindow(
            datetime.fromisoformat(args.start.replace("Z", "+00:00")),
            datetime.fromisoformat(args.end.replace("Z", "+00:00")),
        ),
        output=Path(args.output),
        finding_capture_path=Path(args.finding_capture_path),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

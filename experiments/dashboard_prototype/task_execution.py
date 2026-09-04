"""Fail-closed live executor for disposable dashboard task proofs."""

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
from experiments.dashboard_prototype.task_common import (
    SUPPORTED_SURFACES,
    TASK_MEASURES,
    TaskExperimentId,
    TaskExperimentProof,
    TaskLiveEvidence,
)
from experiments.dashboard_prototype.task_field_audit import (
    REQUIRED_TASK_FIELDS,
    FieldAuthorityState,
)

NAMESPACE = "agent-introspection.dashboard-prototype.v1"
EVENT_NAME = "dashboard_prototype.task_operation.v1"
EXPERIMENT_IDS = tuple(item.value for item in TaskExperimentId)
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z", re.ASCII)
_AVAILABILITY_QUERY_ID = "task-availability-v1"
_FIELD_AUDIT_QUERY_ID = "task-field-audit-v1"


class TaskExecutionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ExtractionWindow:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None or self.start >= self.end:
            raise TaskExecutionError("window must be ordered timezone-aware instants")

    def identity(self) -> str:
        return f"{self.start.astimezone(UTC).isoformat()}..{self.end.astimezone(UTC).isoformat()}"


@dataclass(frozen=True, slots=True)
class RunEnvelope:
    namespace: str
    run_id: str
    window: ExtractionWindow
    proofs: tuple[TaskExperimentProof, ...]
    primitive_event_ids: tuple[str, ...]
    result_event_ids: tuple[str, ...]
    local_oracle: Mapping[str, Mapping[str, object]]
    remote_result: Mapping[str, Mapping[str, object]]
    drain: Mapping[str, int]

    def payload(self) -> dict[str, object]:
        ids = self.primitive_event_ids + self.result_event_ids
        return {
            "namespace": self.namespace,
            "run_id": self.run_id,
            "window": {
                "start": self.window.start.astimezone(UTC).isoformat(),
                "end": self.window.end.astimezone(UTC).isoformat(),
            },
            "proofs": [json.loads(proof.canonical_json()) for proof in self.proofs],
            "domain_local_oracle": _plain(self.local_oracle),
            "remote_result": _plain(self.remote_result),
            "provenance": (
                EvidenceProvenance.FRESH_REAL.value
                if all(proof.provenance is EvidenceProvenance.FRESH_REAL for proof in self.proofs)
                else EvidenceProvenance.RETAINED.value
            ),
            "hashes": {"run_id_hash": canonical_hash(self.run_id)},
            "exact_source_boundary": self.window.identity(),
            "event_map": {
                "primitive": list(self.primitive_event_ids),
                "result": list(self.result_event_ids),
            },
            "drain": dict(self.drain),
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
        raise TaskExecutionError("run ID is not exact and bounded")
    return run_id


def validate_output_path(path: Path) -> Path:
    root = (Path(__file__).parent / "evidence").resolve()
    candidate = path.resolve(strict=False)
    if candidate.suffix != ".json" or root not in candidate.parents:
        raise TaskExecutionError("output must be a JSON file under prototype evidence")
    return candidate


def validate_loopback_endpoint(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise TaskExecutionError("telemetry endpoint must be loopback HTTP")
    return endpoint.rstrip("/")


def extract_live(
    connection: sqlite3.Connection, request: LiveProofRequest
) -> tuple[TaskLiveEvidence, ...]:
    from experiments.dashboard_prototype.task_live_availability import extract as availability
    from experiments.dashboard_prototype.task_live_field_audit import extract as field_audit
    from experiments.dashboard_prototype.task_live_terminal_boundary import (
        extract as terminal_boundary,
    )

    available, fields, terminal = (
        availability(connection, request),
        field_audit(connection, request),
        terminal_boundary(connection, request),
    )
    ordered = _ordered_reducer_blocked(fields.proof, request)
    remote = _remote_calculation_blocked(ordered.proof, terminal.proof, request)
    evidence = (available, fields, ordered, terminal, remote)
    if tuple(item.proof.experiment_id.value for item in evidence) != EXPERIMENT_IDS:
        raise TaskExecutionError("typed proof ordering is incomplete")
    return evidence


def _ordered_reducer_blocked(
    audit: TaskExperimentProof, request: LiveProofRequest
) -> TaskLiveEvidence:
    proof = TaskExperimentProof(
        TaskExperimentId.ORDERED_REDUCER,
        request.run_id,
        ExperimentResult.BLOCKED,
        audit.provenance,
        request.source_boundary,
        {
            "authoritative_operation_candidate_population": 0,
            "field_audit_proof_bound": audit.content_hash()[:16],
        },
        {"authoritative_operation_population_available": False},
        (f"task-field-audit:{audit.content_hash()[:16]}",),
        ("authoritative-operation-identity", "authoritative-operation-order"),
        "Do not construct task-operation candidates from activity aggregates.",
    )
    return TaskLiveEvidence(proof, (), None, {})


def _remote_calculation_blocked(
    ordered: TaskExperimentProof,
    terminal: TaskExperimentProof,
    request: LiveProofRequest,
) -> TaskLiveEvidence:
    provenance = (
        ordered.provenance
        if ordered.provenance is terminal.provenance
        else EvidenceProvenance.RETAINED
    )
    ordered_hash = ordered.content_hash()[:16]
    terminal_hash = terminal.content_hash()[:16]
    proof = TaskExperimentProof(
        TaskExperimentId.REMOTE_CALCULATION,
        request.run_id,
        ExperimentResult.BLOCKED,
        provenance,
        request.source_boundary,
        {
            "authoritative_operation_candidate_population": 0,
            "terminal_candidate_population": 0,
            "ordered_proof_bound": ordered_hash,
            "terminal_proof_bound": terminal_hash,
        },
        {"authoritative_operation_terminal_population_available": False},
        (f"task-ordered:{ordered_hash}", f"task-terminal:{terminal_hash}"),
        ("authoritative-operation-and-terminal-identity",),
        "Do not calculate task outcomes without authoritative operation and terminal candidates.",
    )
    return TaskLiveEvidence(proof, (), None, {})


@dataclass(frozen=True, slots=True)
class _EventSpec:
    kind: str
    query_id: str
    ordinal: int
    attributes: Mapping[str, str | int | float | bool]
    timestamp_ns: int | None = None


def _event(
    proof: TaskExperimentProof, run_id: str, window: ExtractionWindow, spec: _EventSpec
) -> DerivedEvent:
    entity = canonical_hash(
        {
            "namespace": NAMESPACE,
            "run_id_hash": canonical_hash(run_id),
            "experiment_id": proof.experiment_id.value,
            "query_id": spec.query_id,
            "ordinal": spec.ordinal,
            "proof_hash": proof.content_hash(),
            "kind": spec.kind,
        }
    )
    attributes: dict[str, str | int | float | bool] = {
        "dashboard.event_kind": spec.kind,
        "dashboard.experiment_id": proof.experiment_id.value,
        "dashboard.query_id": spec.query_id,
        "dashboard.run_id_hash": canonical_hash(run_id),
        "dashboard.proof_hash": proof.content_hash(),
        **dict(spec.attributes),
    }
    if spec.kind == "primitive":
        attributes["dashboard.cohort"] = _primitive_cohort(attributes)
    return DerivedEvent(
        NAMESPACE,
        entity,
        1,
        spec.ordinal,
        EVENT_NAME,
        attributes,
        int(window.end.timestamp() * 1_000_000_000)
        if spec.timestamp_ns is None
        else spec.timestamp_ns,
    )


def _safe_attributes(
    item: TaskLiveEvidence, dimensions: Mapping[str, object], measures: Mapping[str, object]
) -> dict[str, str | int | float | bool]:
    if item.proof.experiment_id is TaskExperimentId.AVAILABILITY:
        allowed, required = (
            {
                "producer",
                "surface",
                "measure",
                "route_state",
                "capability",
                "time_domain",
                "population",
                "redaction_boundary",
            },
            {"route_count", "activity_population", "resolved_count", "unresolved_count"},
        )
    elif item.proof.experiment_id is TaskExperimentId.FIELD_AUDIT:
        allowed, required = (
            {"producer", "surface", "field", "authority_state"},
            {"classification_count"},
        )
    else:
        raise TaskExecutionError("unsafe task primitive")
    if set(dimensions) != allowed or set(measures) != required:
        raise TaskExecutionError("unsafe task primitive")
    attrs: dict[str, str | int | float | bool] = {}
    for key, value in dimensions.items():
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise TaskExecutionError("primitive dimension is not scalar")
        attrs[f"dashboard.{key}"] = value
    for key, value in measures.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TaskExecutionError("primitive measure is not scalar")
        attrs[f"dashboard.{key}"] = value
    _reject_unsafe(attrs)
    return attrs


def primitive_events(
    evidence: Sequence[TaskLiveEvidence], run_id: str, window: ExtractionWindow
) -> tuple[DerivedEvent, ...]:
    return tuple(
        _event(
            item.proof,
            run_id,
            window,
            _EventSpec(
                "primitive",
                _AVAILABILITY_QUERY_ID
                if item.proof.experiment_id is TaskExperimentId.AVAILABILITY
                else _FIELD_AUDIT_QUERY_ID,
                primitive.ordinal,
                _safe_attributes(item, primitive.dimensions, primitive.measures),
                int(primitive.source_time.timestamp() * 1_000_000_000),
            ),
        )
        for item in evidence
        if item.proof.experiment_id in {TaskExperimentId.AVAILABILITY, TaskExperimentId.FIELD_AUDIT}
        for primitive in item.primitives
    )


def result_events(
    proofs: Sequence[TaskExperimentProof], run_id: str, window: ExtractionWindow
) -> tuple[DerivedEvent, ...]:
    if tuple(proof.experiment_id.value for proof in proofs) != EXPERIMENT_IDS:
        raise TaskExecutionError("five typed task results required")
    return tuple(
        _event(
            proof,
            run_id,
            window,
            _EventSpec("result", "result-v1", ordinal, {"dashboard.result": proof.result.value}),
        )
        for ordinal, proof in enumerate(proofs, 1)
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
        raise TaskExecutionError("exact outbox drain mismatch")
    return {key: int(result[key]) for key in ("selected", "delivered", "pending")}


def _verify_ids(client: _RemoteClient, events: Sequence[DerivedEvent]) -> None:
    expected = {event.event_id for event in events}
    for _ in range(20):
        if set(remote_event_ids(client, tuple(events))) == expected:
            return
        sleep(0.5)
    raise TaskExecutionError("exact remote event IDs mismatch")


def _primitive_cohort(attributes: Mapping[str, str | int | float | bool]) -> str:
    if attributes["dashboard.experiment_id"] == TaskExperimentId.AVAILABILITY.value:
        return ".".join(
            str(attributes[f"dashboard.{key}"])
            for key in (
                "producer",
                "surface",
                "measure",
                "route_state",
                "capability",
                "time_domain",
                "population",
                "redaction_boundary",
            )
        )
    return ".".join(
        str(attributes[f"dashboard.{key}"])
        for key in ("producer", "surface", "field", "authority_state")
    )


def _primitive_oracle(
    primitives: Sequence[DerivedEvent], experiment: TaskExperimentId
) -> dict[str, Mapping[str, int]]:
    oracle: dict[str, Mapping[str, int]] = {}
    for event in primitives:
        if event.attributes["dashboard.experiment_id"] != experiment.value:
            continue
        cohort = _primitive_cohort(event.attributes)
        if cohort in oracle:
            raise TaskExecutionError("duplicate primitive cohort")
        if experiment is TaskExperimentId.AVAILABILITY:
            oracle[cohort] = {
                "route_count": int(event.attributes["dashboard.route_count"]),
                "activity_population": int(event.attributes["dashboard.activity_population"]),
                "resolved_count": int(event.attributes["dashboard.resolved_count"]),
                "unresolved_count": int(event.attributes["dashboard.unresolved_count"]),
            }
        else:
            oracle[cohort] = {
                "classification_count": int(event.attributes["dashboard.classification_count"])
            }
    return oracle


def remote_calculations(
    client: _RemoteClient,
    primitives: Sequence[DerivedEvent],
    window: ExtractionWindow,
    run_id: str,
) -> dict[str, Mapping[str, object]]:
    output: dict[str, Mapping[str, object]] = {}
    for experiment, query_id, measures in (
        (
            TaskExperimentId.AVAILABILITY,
            _AVAILABILITY_QUERY_ID,
            (
                "route_count",
                "activity_population",
                "resolved_count",
                "unresolved_count",
            ),
        ),
        (TaskExperimentId.FIELD_AUDIT, _FIELD_AUDIT_QUERY_ID, ("classification_count",)),
    ):
        events = tuple(
            event
            for event in primitives
            if event.attributes["dashboard.experiment_id"] == experiment.value
        )
        if not events:
            output[experiment.value] = {}
            continue
        parameters: dict[str, str | int] = {
            "start_ns": int(window.start.timestamp() * 1_000_000_000),
            "end_ns": int(window.end.timestamp() * 1_000_000_000),
            "run_id_hash": canonical_hash(run_id),
            "query_id": query_id,
            "event_name": EVENT_NAME,
        }
        parameters.update({f"event_{index}": event.event_id for index, event in enumerate(events)})
        summed = ", ".join(f"sum({measure}) AS {measure}" for measure in measures)
        selected = ", ".join(
            f"any(attributes_number['dashboard.{measure}']) AS {measure}" for measure in measures
        )
        consistency = ", ".join(
            (
                "attributes_string['dashboard.cohort']",
                *(f"attributes_number['dashboard.{measure}']" for measure in measures),
            )
        )
        placeholders = ", ".join(f"{{event_{index}:String}}" for index in range(len(events)))
        sql = (
            "SELECT cohort, "
            f"{summed} FROM ("
            "SELECT attributes_string['event.id'] AS event_id, "
            "any(attributes_string['dashboard.cohort']) AS cohort, "
            f"{selected} FROM signoz_logs.distributed_logs_v2 "
            "WHERE timestamp > {start_ns:UInt64} AND timestamp <= {end_ns:UInt64} "
            "AND attributes_string['event.name'] = {event_name:String} "
            "AND attributes_string['dashboard.run_id_hash'] = {run_id_hash:String} "
            "AND attributes_string['dashboard.query_id'] = {query_id:String} "
            f"AND attributes_string['event.id'] IN ({placeholders}) "
            "GROUP BY event_id "
            f"HAVING uniqExact(tuple({consistency})) = 1"
            ") GROUP BY cohort"
        )
        calculation: dict[str, Mapping[str, int]] = {}
        for row in client.query(sql, parameters):
            cohort = row.get("cohort")
            values = {measure: row.get(measure) for measure in measures}
            if not isinstance(cohort, str) or not cohort or cohort in calculation:
                raise TaskExecutionError("invalid remote task calculation row")
            normalized: dict[str, int] = {}
            for measure, value in values.items():
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise TaskExecutionError("invalid remote task calculation row")
                normalized[measure] = value
            calculation[cohort] = normalized
        output[experiment.value] = calculation
    return output


def _local_oracle(
    proofs: Sequence[TaskExperimentProof], primitives: Sequence[DerivedEvent]
) -> dict[str, Mapping[str, object]]:
    del proofs
    return {
        TaskExperimentId.AVAILABILITY.value: _primitive_oracle(
            primitives, TaskExperimentId.AVAILABILITY
        ),
        TaskExperimentId.FIELD_AUDIT.value: _primitive_oracle(
            primitives, TaskExperimentId.FIELD_AUDIT
        ),
    }


def _final_proofs(
    evidence: Sequence[TaskLiveEvidence],
    remote: Mapping[str, Mapping[str, object]],
    primitives: Sequence[DerivedEvent],
) -> tuple[TaskExperimentProof, ...]:
    if remote.get(TaskExperimentId.AVAILABILITY.value, {}) != _primitive_oracle(
        primitives, TaskExperimentId.AVAILABILITY
    ) or remote.get(TaskExperimentId.FIELD_AUDIT.value, {}) != _primitive_oracle(
        primitives, TaskExperimentId.FIELD_AUDIT
    ):
        raise TaskExecutionError("remote calculation reconciliation mismatch")
    primitive_ids = {
        experiment: tuple(
            event.event_id
            for event in primitives
            if event.attributes["dashboard.experiment_id"] == experiment.value
        )
        for experiment in (TaskExperimentId.AVAILABILITY, TaskExperimentId.FIELD_AUDIT)
    }
    proofs = [item.proof for item in evidence]
    for index, experiment in (
        (0, TaskExperimentId.AVAILABILITY),
        (1, TaskExperimentId.FIELD_AUDIT),
    ):
        proofs[index] = replace(
            proofs[index],
            assertions={
                **proofs[index].assertions,
                "remote_calculation_reconciled": True,
            },
            evidence_ids=(*proofs[index].evidence_ids, *primitive_ids[experiment]),
        )
    proofs[4] = replace(
        proofs[4],
        assertions={
            **proofs[4].assertions,
            "classification_primitives_reconciled": True,
        },
        evidence_ids=(
            *proofs[4].evidence_ids,
            *primitive_ids[TaskExperimentId.AVAILABILITY],
            *primitive_ids[TaskExperimentId.FIELD_AUDIT],
        ),
    )
    return tuple(proofs)


def _deep_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _deep_value(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_deep_value(item) for item in value]
    _reject_unsafe(value)
    return value


def _plain(
    value: Mapping[str, Mapping[str, object]],
) -> dict[str, dict[str, object]]:
    plain: dict[str, dict[str, object]] = {}
    for key, item in sorted(value.items()):
        converted = _deep_value(item)
        if not isinstance(converted, dict):
            raise TaskExecutionError("oracle value is not an object")
        plain[key] = converted
    return plain


def _safe_cohort(key: str, value: object) -> bool:
    parts = key.split(".")
    if not isinstance(value, Mapping):
        return False
    if len(parts) == 4:
        return (
            (parts[0], parts[1]) in SUPPORTED_SURFACES
            and parts[2] in REQUIRED_TASK_FIELDS
            and parts[3] in {state.value for state in FieldAuthorityState}
        )
    return (
        len(parts) == 8 and (parts[0], parts[1]) in SUPPORTED_SURFACES and parts[2] in TASK_MEASURES
    )


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
            label = str(key)
            if (
                label != "cleanup_selector"
                and not _safe_cohort(label, item)
                and any(word in label.lower() for word in prohibited)
            ):
                raise TaskExecutionError("unsafe evidence field")
            _reject_unsafe(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_unsafe(item)
    elif isinstance(value, float) and not math.isfinite(value):
        raise TaskExecutionError("unsafe scalar")
    elif not isinstance(value, (str, int, float, bool, type(None))):
        raise TaskExecutionError("non-JSON evidence value")


def run(
    *, run_id: str, start: datetime, end: datetime, output: Path, config: AppConfig | None = None
) -> RunEnvelope:
    validate_run_id(run_id)
    output = validate_output_path(output)
    window = ExtractionWindow(start, end)
    config = config or load_config()
    endpoint = validate_loopback_endpoint(config.signoz.otlp_http_endpoint)
    connection = sqlite3.connect(f"file:{config.database.path}?mode=rw", uri=True)
    try:
        evidence = extract_live(connection, LiveProofRequest(run_id, start, end))
        primitives = primitive_events(evidence, run_id, window)
        client = ClickHouseClient(
            docker_context=config.signoz.docker_context,
            container=config.signoz.clickhouse_container,
        )
        enqueue_events(connection, list(primitives))
        primitive_drain = _exact_drain(connection, primitives, endpoint)
        _verify_ids(client, primitives)
        remote = remote_calculations(client, primitives, window, run_id)
        proofs = _final_proofs(evidence, remote, primitives)
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
            _local_oracle(proofs, primitives),
            remote,
            {
                "primitive_selected": primitive_drain["selected"],
                "primitive_delivered": primitive_drain["delivered"],
                "result_selected": result_drain["selected"],
                "result_delivered": result_drain["delivered"],
            },
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(envelope.canonical_json() + "\n", encoding="utf-8")
        return envelope
    finally:
        connection.close()


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

"""Fail-closed live executor for dashboard request proofs."""

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
from experiments.dashboard_prototype.request_common import (
    RequestExperimentId,
    RequestExperimentProof,
    RequestLiveEvidence,
)
from experiments.dashboard_prototype.request_field_audit import (
    PRODUCER_SURFACES,
    REQUIRED_REQUEST_FIELDS,
    FieldAuthorityState,
)

NAMESPACE = "agent-introspection.dashboard-prototype.v1"
EVENT_NAME = "dashboard_prototype.request_snapshot.v1"
EXPERIMENT_IDS = tuple(item.value for item in RequestExperimentId)
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z", re.ASCII)
_FIELD_AUDIT_QUERY_ID = "request-field-audit-v1"
_FIELD_AUDIT_SQL = """
SELECT attributes_string['dashboard.producer'] AS producer,
 attributes_string['dashboard.surface'] AS surface,
 attributes_string['dashboard.field'] AS field,
 attributes_string['dashboard.authority_state'] AS state,
 toUInt32(sum(attributes_number['dashboard.classification_count'])) AS count
FROM signoz_logs.distributed_logs_v2
WHERE timestamp > {start_ns:UInt64} AND timestamp <= {end_ns:UInt64}
 AND ts_bucket_start BETWEEN {start_bucket:UInt64} AND {end_bucket:UInt64}
 AND resource.`service.name`::String = 'agent-introspection'
 AND attributes_string['event.name'] = {event_name:String}
 AND attributes_string['dashboard.event_kind'] = 'primitive'
 AND attributes_string['dashboard.experiment_id'] = 'E-Request-1'
 AND attributes_string['dashboard.run_id_hash'] = {run_id_hash:String}
 AND attributes_string['dashboard.query_id'] = {query_id:String}
 AND attributes_string['event.id'] IN ({event_ids})
GROUP BY producer, surface, field, state ORDER BY producer, surface, field, state
""".strip()


class RequestExecutionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ExtractionWindow:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None or self.start >= self.end:
            raise RequestExecutionError("window must be ordered timezone-aware instants")

    def identity(self) -> str:
        return f"{self.start.astimezone(UTC).isoformat()}..{self.end.astimezone(UTC).isoformat()}"


@dataclass(frozen=True, slots=True)
class RunEnvelope:
    namespace: str
    run_id: str
    window: ExtractionWindow
    proofs: tuple[RequestExperimentProof, ...]
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
        raise RequestExecutionError("run ID is not exact and bounded")
    return run_id


def validate_output_path(path: Path) -> Path:
    root = (Path(__file__).parent / "evidence").resolve()
    candidate = path.resolve(strict=False)
    if candidate.suffix != ".json" or root not in candidate.parents:
        raise RequestExecutionError("output must be a JSON file under prototype evidence")
    return candidate


def validate_loopback_endpoint(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise RequestExecutionError("telemetry endpoint must be loopback HTTP")
    return endpoint.rstrip("/")


def extract_live(
    connection: sqlite3.Connection, request: LiveProofRequest
) -> tuple[RequestLiveEvidence, ...]:
    from experiments.dashboard_prototype.request_live_field_audit import (
        extract as extract_field_audit,
    )

    audit = extract_field_audit(connection, request)
    attempt = _request_attempt_blocked(audit.proof, request)
    remote = _remote_calculation_blocked(audit.proof, request)
    evidence = (audit, attempt, remote)
    if tuple(item.proof.experiment_id.value for item in evidence) != EXPERIMENT_IDS:
        raise RequestExecutionError("typed proof ordering is incomplete")
    return evidence


def _request_attempt_blocked(
    audit: RequestExperimentProof, request: LiveProofRequest
) -> RequestLiveEvidence:
    proof = RequestExperimentProof(
        RequestExperimentId.REQUEST_ATTEMPT,
        request.run_id,
        ExperimentResult.BLOCKED,
        EvidenceProvenance.FRESH_REAL,
        request.source_boundary,
        {"authoritative_candidate_count": 0, "audit_proof_bound": audit.content_hash()[:16]},
        {"authoritative_request_attempt_identity_available": False},
        (f"request-audit:{audit.content_hash()[:16]}",),
        (
            "authoritative-logical-request-identity",
            "authoritative-attempt-identity",
            "authoritative-accounting-identity",
        ),
        (
            "Do not construct request candidates without authoritative request, "
            "attempt, and accounting identity."
        ),
    )
    return RequestLiveEvidence(proof, (), None, {})


def _remote_calculation_blocked(
    audit: RequestExperimentProof, request: LiveProofRequest
) -> RequestLiveEvidence:
    proof = RequestExperimentProof(
        RequestExperimentId.REMOTE_CALCULATION,
        request.run_id,
        ExperimentResult.BLOCKED,
        EvidenceProvenance.FRESH_REAL,
        request.source_boundary,
        {"authoritative_candidate_count": 0, "audit_proof_bound": audit.content_hash()[:16]},
        {"authoritative_request_attempt_identity_available": False},
        (f"request-audit:{audit.content_hash()[:16]}",),
        ("authoritative-logical-request-and-attempt-identity",),
        "Preserve E-Request-1 and do not calculate request records without authority.",
    )
    return RequestLiveEvidence(proof, (), None, {})


@dataclass(frozen=True, slots=True)
class _EventSpec:
    kind: str
    query_id: str
    ordinal: int
    attributes: Mapping[str, str | int | float | bool]
    timestamp_ns: int | None = None


def _event(
    proof: RequestExperimentProof, run_id: str, window: ExtractionWindow, spec: _EventSpec
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
            "dashboard.proof_hash": proof.content_hash(),
            **dict(spec.attributes),
        },
        int(window.end.timestamp() * 1_000_000_000)
        if spec.timestamp_ns is None
        else spec.timestamp_ns,
    )


def _safe_attributes(
    item: RequestLiveEvidence, dimensions: Mapping[str, object], measures: Mapping[str, object]
) -> dict[str, str | int | float | bool]:
    if (
        item.proof.experiment_id is not RequestExperimentId.FIELD_AUDIT
        or set(dimensions) - {"producer", "surface", "field", "authority_state"}
        or set(measures) != {"classification_count"}
    ):
        raise RequestExecutionError("unsafe request primitive")
    attrs: dict[str, str | int | float | bool] = {}
    for key, value in dimensions.items():
        if isinstance(value, bool):
            attrs[f"dashboard.{key}"] = str(value).lower()
        elif isinstance(value, (str, int, float)):
            attrs[f"dashboard.{key}"] = value
        else:
            raise RequestExecutionError("primitive dimension is not scalar")
    value = measures["classification_count"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RequestExecutionError("primitive measure is not scalar")
    attrs["dashboard.classification_count"] = value
    _reject_unsafe(attrs)
    return attrs


def primitive_events(
    evidence: Sequence[RequestLiveEvidence], run_id: str, window: ExtractionWindow
) -> tuple[DerivedEvent, ...]:
    return tuple(
        _event(
            item.proof,
            run_id,
            window,
            _EventSpec(
                "primitive",
                _FIELD_AUDIT_QUERY_ID,
                primitive.ordinal,
                _safe_attributes(item, primitive.dimensions, primitive.measures),
                int(primitive.source_time.timestamp() * 1_000_000_000),
            ),
        )
        for item in evidence
        if item.proof.experiment_id is RequestExperimentId.FIELD_AUDIT
        for primitive in item.primitives
    )


def result_events(
    proofs: Sequence[RequestExperimentProof], run_id: str, window: ExtractionWindow
) -> tuple[DerivedEvent, ...]:
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
            isinstance(result.get(k), bool) or not isinstance(result.get(k), int)
            for k in ("selected", "delivered", "pending")
        )
        or (result["selected"], result["delivered"], result["pending"])
        != (len(events), len(events), 0)
    ):
        raise RequestExecutionError("exact outbox drain mismatch")
    return {key: int(result[key]) for key in ("selected", "delivered", "pending")}


def _verify_ids(client: _RemoteClient, events: Sequence[DerivedEvent]) -> None:
    if not events:
        return
    expected = tuple(events)
    expected_ids = {event.event_id for event in expected}
    for _ in range(20):
        if set(remote_event_ids(client, expected)) == expected_ids:
            return
        sleep(0.5)
    raise RequestExecutionError("exact remote event IDs mismatch")


def _params(
    events: Sequence[DerivedEvent], window: ExtractionWindow, run_id: str
) -> tuple[str, dict[str, str | int]]:
    query_ids = {str(event.attributes.get("dashboard.query_id", "")) for event in events}
    if query_ids != {_FIELD_AUDIT_QUERY_ID}:
        raise RequestExecutionError("field-audit primitive query identity mismatch")
    parameters: dict[str, str | int] = {
        "start_ns": int(window.start.timestamp() * 1_000_000_000),
        "end_ns": int(window.end.timestamp() * 1_000_000_000),
        "end_bucket": int(window.end.timestamp()),
        "event_name": EVENT_NAME,
        "run_id_hash": canonical_hash(run_id),
        "start_bucket": max(0, int(window.start.timestamp()) - 1800),
        "query_id": query_ids.pop(),
    }
    parameters.update({f"event_{i}": event.event_id for i, event in enumerate(events)})
    return ", ".join(f"{{event_{i}:String}}" for i in range(len(events))), parameters


def remote_calculations(
    client: _RemoteClient, primitives: Sequence[DerivedEvent], window: ExtractionWindow, run_id: str
) -> dict[str, Mapping[str, object]]:
    if not primitives:
        return {RequestExperimentId.FIELD_AUDIT.value: {}}
    placeholders, parameters = _params(primitives, window, run_id)
    rows = tuple(client.query(_FIELD_AUDIT_SQL.replace("{event_ids}", placeholders), parameters))
    output: dict[str, int] = {}
    for row in rows:
        producer, surface, field, state, count = (
            row.get("producer"),
            row.get("surface"),
            row.get("field"),
            row.get("state"),
            row.get("count"),
        )
        if (
            not isinstance(producer, str)
            or not producer
            or not isinstance(surface, str)
            or not surface
            or not isinstance(field, str)
            or not field
            or not isinstance(state, str)
            or not state
        ):
            raise RequestExecutionError("invalid remote field-audit row")
        if (
            (producer, surface) not in PRODUCER_SURFACES
            or field not in REQUIRED_REQUEST_FIELDS
            or state not in {item.value for item in FieldAuthorityState}
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
        ):
            raise RequestExecutionError("invalid remote field-audit row")
        key = _classification_cohort(producer, surface, field, state)
        if key in output:
            raise RequestExecutionError("duplicate remote field-audit row")
        output[key] = count
    return {RequestExperimentId.FIELD_AUDIT.value: output}


def _classification_cohort(producer: str, surface: str, field: str, state: str) -> str:
    return f"{producer}.{surface}.{field}.{state}"


def _field_audit_oracle(primitives: Sequence[DerivedEvent]) -> dict[str, int]:
    return {
        _classification_cohort(
            str(event.attributes["dashboard.producer"]),
            str(event.attributes["dashboard.surface"]),
            str(event.attributes["dashboard.field"]),
            str(event.attributes["dashboard.authority_state"]),
        ): int(event.attributes["dashboard.classification_count"])
        for event in primitives
    }


def _local_oracle(
    proofs: Sequence[RequestExperimentProof], primitives: Sequence[DerivedEvent]
) -> dict[str, Mapping[str, object]]:
    return {
        RequestExperimentId.FIELD_AUDIT.value: _field_audit_oracle(primitives),
        **{
            proof.experiment_id.value: dict(proof.metrics)
            for proof in proofs
            if proof.experiment_id is not RequestExperimentId.FIELD_AUDIT
        },
    }


def _final_proofs(
    evidence: Sequence[RequestLiveEvidence],
    remote: Mapping[str, Mapping[str, object]],
    primitives: Sequence[DerivedEvent],
) -> tuple[RequestExperimentProof, ...]:
    if remote.get(RequestExperimentId.FIELD_AUDIT.value, {}) != _field_audit_oracle(primitives):
        raise RequestExecutionError("remote calculation reconciliation mismatch")
    final_audit = replace(
        evidence[0].proof,
        assertions={
            **evidence[0].proof.assertions,
            "remote_calculation_reconciled": True,
        },
    )
    audit_hash = final_audit.content_hash()[:16]
    return (
        final_audit,
        *tuple(
            replace(
                item.proof,
                metrics={**item.proof.metrics, "audit_proof_bound": audit_hash},
                evidence_ids=(f"request-audit:{audit_hash}",),
            )
            for item in evidence[1:]
        ),
    )


def _deep_plain(value: Mapping[str, object]) -> dict[str, object]:
    return {str(key): _deep_value(item) for key, item in sorted(value.items())}


def _deep_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _deep_plain(value)
    if isinstance(value, (list, tuple)):
        return [_deep_value(item) for item in value]
    _reject_unsafe(value)
    return value


def _plain(value: Mapping[str, Mapping[str, object]]) -> dict[str, dict[str, object]]:
    return {key: _deep_plain(item) for key, item in sorted(value.items())}


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
        pd = _exact_drain(connection, primitives, endpoint)
        _verify_ids(client, primitives)
        remote = remote_calculations(client, primitives, window, run_id)
        proofs = _final_proofs(evidence, remote, primitives)
        results = result_events(proofs, run_id, window)
        enqueue_events(connection, list(results))
        rd = _exact_drain(connection, results, endpoint)
        _verify_ids(client, results)
        envelope = RunEnvelope(
            NAMESPACE,
            run_id,
            window,
            proofs,
            tuple(e.event_id for e in primitives),
            tuple(e.event_id for e in results),
            _local_oracle(proofs, primitives),
            remote,
            {
                "primitive_selected": pd["selected"],
                "primitive_delivered": pd["delivered"],
                "result_selected": rd["selected"],
                "result_delivered": rd["delivered"],
            },
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(envelope.canonical_json() + "\n", encoding="utf-8")
        return envelope
    finally:
        connection.close()


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
                and not _safe_classification_cohort(label, item)
                and any(word in label.lower() for word in prohibited)
            ):
                raise RequestExecutionError("unsafe evidence field")
            _reject_unsafe(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_unsafe(item)
    elif isinstance(value, float) and not math.isfinite(value):
        raise RequestExecutionError("unsafe scalar")
    elif not isinstance(value, (str, int, float, bool, type(None))):
        raise RequestExecutionError("non-JSON evidence value")


def _safe_classification_cohort(key: str, value: object) -> bool:
    parts = key.split(".")
    return (
        len(parts) == 4
        and (parts[0], parts[1]) in PRODUCER_SURFACES
        and parts[2] in REQUIRED_REQUEST_FIELDS
        and parts[3] in {state.value for state in FieldAuthorityState}
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    )


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

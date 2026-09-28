"""Native OTLP transport for immutable measured pipeline primitives."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from agent_introspection.telemetry import DerivedEvent, enqueue_events
from experiments.dashboard_prototype import contracts

_PIPELINE_EVENT_NAME = "dashboard_prototype.pipeline_snapshot.v1"
_SCALAR_TYPES = (str, int, float, bool)
_RESERVED_PAYLOAD_KEYS = frozenset(
    {
        "event.id",
        "event.scope",
        "entity.id",
        "entity.version",
        "event.sequence",
        "event.name",
        "timestamp_ns",
    }
)
_TRANSPORT_ATTRIBUTE_KEYS = frozenset(
    {
        "dashboard.event_kind",
        "dashboard.namespace",
        "dashboard.run_id_hash",
        "dashboard.experiment_id",
        "dashboard.producer",
        "dashboard.native_session_id",
    }
)


@dataclass(frozen=True, slots=True)
class EvidenceTransportIdentity:
    """The exact immutable identity shared by one native evidence stream."""

    namespace: str
    run_id: str
    experiment_id: str
    producer: str
    native_session_id: str

    def __post_init__(self) -> None:
        if self.namespace != contracts.EXPERIMENT_NAMESPACE:
            raise ValueError("evidence transport must use the canonical namespace")
        if self.producer not in contracts.SUPPORTED_PRODUCERS:
            raise ValueError("evidence transport producer is unsupported")
        if not all(
            isinstance(value, str) and value
            for value in (self.run_id, self.experiment_id, self.native_session_id)
        ):
            raise ValueError("evidence transport identity fields must be nonempty strings")


@dataclass(frozen=True, slots=True)
class EvidencePrimitive:
    """One source-timestamped scalar OTLP payload contribution."""

    timestamp_ns: int
    attributes: Mapping[str, str | int | float | bool]

    def __post_init__(self) -> None:
        if not isinstance(self.timestamp_ns, int) or isinstance(self.timestamp_ns, bool):
            raise TypeError("evidence primitive timestamp_ns must be an integer")
        if not isinstance(self.attributes, Mapping):
            raise TypeError("evidence primitive attributes must be a mapping")
        attributes = dict(self.attributes)
        if any(
            not isinstance(key, str)
            or not key
            or key in _RESERVED_PAYLOAD_KEYS
            or key in _TRANSPORT_ATTRIBUTE_KEYS
            for key in attributes
        ):
            raise ValueError(
                "evidence primitive attributes may not override transport payload fields"
            )
        if any(
            not isinstance(value, _SCALAR_TYPES)
            or (isinstance(value, float) and not math.isfinite(value))
            for value in attributes.values()
        ):
            raise TypeError("evidence primitive attributes must be finite scalar values")
        object.__setattr__(self, "attributes", MappingProxyType(attributes))


@dataclass(frozen=True, slots=True)
class _EvidencePrimitiveEvent(DerivedEvent):
    event_id_inputs: Mapping[str, object]

    @property
    def event_id(self) -> str:
        return contracts.derive_event_id(self.event_id_inputs)


def enqueue_evidence_primitives(
    connection: sqlite3.Connection,
    identity: EvidenceTransportIdentity,
    primitives: Sequence[EvidencePrimitive],
) -> tuple[str, ...]:
    """Enqueue source-timestamped primitives without altering the caller transaction."""
    if not connection.in_transaction:
        raise ValueError("evidence transport requires a caller-owned transaction")
    events: list[DerivedEvent] = []
    for ordinal, primitive in enumerate(primitives):
        event_id_inputs = MappingProxyType(
            {
                "experiment_id": identity.experiment_id,
                "producer": identity.producer,
                "native_session_id": identity.native_session_id,
                "event_id_ordinal": ordinal,
            }
        )
        event_id = contracts.derive_event_id(event_id_inputs)
        events.append(
            _EvidencePrimitiveEvent(
                scope=identity.namespace,
                entity_id=event_id,
                entity_version=1,
                event_sequence=ordinal,
                event_name=_PIPELINE_EVENT_NAME,
                attributes={
                    "dashboard.event_kind": "primitive",
                    "dashboard.namespace": identity.namespace,
                    "dashboard.run_id_hash": _hash_run_id(identity.run_id),
                    "dashboard.experiment_id": identity.experiment_id,
                    "dashboard.producer": identity.producer,
                    "dashboard.native_session_id": identity.native_session_id,
                    **primitive.attributes,
                },
                timestamp_ns=primitive.timestamp_ns,
                event_id_inputs=event_id_inputs,
            )
        )
    return tuple(enqueue_events(connection, events))


def _hash_run_id(run_id: str) -> str:
    return hashlib.sha256(
        json.dumps(run_id, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    ).hexdigest()

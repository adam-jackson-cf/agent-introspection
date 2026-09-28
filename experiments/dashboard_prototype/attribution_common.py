"""Shared result envelope specialization for disposable attribution experiments."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from experiments.dashboard_prototype.experiment_common import ExperimentProof


class AttributionExperimentId(StrEnum):
    """Registered attribution experiment identities."""

    BASELINE = "E-Attribution-1"
    CODEX_APP_SERVER = "E-Attribution-2"
    CLAUDE_BOUNDARY = "E-Attribution-3"
    LIFECYCLE_DELAY = "E-Attribution-4"
    LATE_CONTEXT = "E-Attribution-5"


AttributionExperimentProof = ExperimentProof[AttributionExperimentId]


class AttributionRowState(StrEnum):
    """Whether one matrix row has complete, independently promotable authority."""

    READY = "EvidenceBundle-ready"
    BLOCKED = "Blocked"


_ROW_IDS = frozenset(("A07", "A08", "A09"))
_ROW_STAGES = ("source", "reducer", "delivery")
_ROW_SCALAR = str | int | bool | None
_ROW_EVENT_INPUT_FIELDS = frozenset(
    (
        "experiment_id",
        "row_id",
        "producer",
        "native_session_id",
        "source_id",
        "reducer_id",
        "entity_version",
        "event_id_ordinal",
    )
)


@dataclass(frozen=True, slots=True)
class AttributionRowMemberEvidence:
    """The exact native lineage and three immutable events for one row member."""

    native_session_id: str
    source_id: str
    reducer_id: str
    event_id_inputs: Mapping[str, Mapping[str, object]]
    event_ids: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class AttributionRowEvidence:
    """One row-local full-population execution output; no member is collapsed."""

    experiment_id: AttributionExperimentId
    row_id: str
    producer: str
    state: AttributionRowState
    blocked_reason: str | None
    members: tuple[AttributionRowMemberEvidence, ...] | None
    direct_remote_sql_parameters: Mapping[str, _ROW_SCALAR] | None
    direct_remote_sql_parameter_types: Mapping[str, str] | None
    direct_remote_result: Mapping[str, _ROW_SCALAR] | None
    direct_remote_result_types: Mapping[str, str] | None
    oracle_parameters: Mapping[str, _ROW_SCALAR] | None
    oracle_parameter_types: Mapping[str, str] | None
    oracle_result: Mapping[str, _ROW_SCALAR] | None
    oracle_result_types: Mapping[str, str] | None

    def __post_init__(self) -> None:
        if self.row_id not in _ROW_IDS or not isinstance(self.producer, str) or not self.producer:
            raise ValueError("row evidence must target a producer A07--A09 obligation")
        if self.state is AttributionRowState.BLOCKED:
            if (
                self.blocked_reason is None
                or self.members is not None
                or any(value is not None for value in _evidence_bindings(self))
            ):
                raise ValueError("blocked rows retain no evidence bundle")
            return
        if (
            self.blocked_reason is not None
            or not self.members
            or any(value is None for value in _evidence_bindings(self))
        ):
            raise ValueError("ready rows require complete evidence bindings")
        identities = set()
        for member in self.members:
            identity = (member.native_session_id, member.source_id, member.reducer_id)
            if identity in identities:
                raise ValueError("row members must retain unique exact lineages")
            identities.add(identity)
            _validate_member(self, member)
        _validate_row_scalars(
            self.direct_remote_sql_parameters,
            self.direct_remote_sql_parameter_types,
            "direct remote SQL parameters",
        )
        _validate_row_scalars(
            self.direct_remote_result, self.direct_remote_result_types, "direct remote result"
        )
        _validate_row_scalars(
            self.oracle_parameters, self.oracle_parameter_types, "oracle parameters"
        )
        _validate_row_scalars(self.oracle_result, self.oracle_result_types, "oracle result")
        if self.direct_remote_result != self.oracle_result:
            raise ValueError("row direct remote result must equal its independent oracle")

    def deterministic_id(self) -> str:
        """Return the immutable identity of this full-population row obligation."""
        return hashlib.sha256(
            json.dumps(
                {
                    "experiment_id": self.experiment_id.value,
                    "row_id": self.row_id,
                    "producer": self.producer,
                    "members": [
                        {
                            "native_session_id": item.native_session_id,
                            "source_id": item.source_id,
                            "reducer_id": item.reducer_id,
                            "event_ids": dict(item.event_ids),
                        }
                        for item in self.members or ()
                    ],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()


def _evidence_bindings(row: AttributionRowEvidence) -> tuple[object | None, ...]:
    return (
        row.direct_remote_sql_parameters,
        row.direct_remote_sql_parameter_types,
        row.direct_remote_result,
        row.direct_remote_result_types,
        row.oracle_parameters,
        row.oracle_parameter_types,
        row.oracle_result,
        row.oracle_result_types,
    )


def _validate_member(row: AttributionRowEvidence, member: AttributionRowMemberEvidence) -> None:
    if (
        not all(
            isinstance(value, str) and value
            for value in (member.native_session_id, member.source_id, member.reducer_id)
        )
        or set(member.event_id_inputs) != set(_ROW_STAGES)
        or set(member.event_ids) != set(_ROW_STAGES)
    ):
        raise ValueError("row members require exact lineage and all stage IDs")
    for ordinal, stage in enumerate(_ROW_STAGES):
        event_input = member.event_id_inputs[stage]
        if set(event_input) != _ROW_EVENT_INPUT_FIELDS or (
            event_input.get("experiment_id") != row.experiment_id.value
            or event_input.get("row_id") != row.row_id
            or event_input.get("producer") != row.producer
            or event_input.get("native_session_id") != member.native_session_id
            or event_input.get("source_id") != member.source_id
            or event_input.get("reducer_id") != member.reducer_id
            or event_input.get("entity_version") != 3
            or event_input.get("event_id_ordinal") != ordinal
            or not isinstance(member.event_ids[stage], str)
            or not member.event_ids[stage]
        ):
            raise ValueError("row member event IDs must bind exact native lineage")


def _validate_row_scalars(
    values: Mapping[str, _ROW_SCALAR] | None, types: Mapping[str, str] | None, label: str
) -> None:
    if not values or types is None or set(values) != set(types):
        raise ValueError(f"{label} requires exact scalar types")
    for key, value in values.items():
        actual = (
            "null"
            if value is None
            else "boolean"
            if isinstance(value, bool)
            else "integer"
            if isinstance(value, int)
            else "string"
            if isinstance(value, str)
            else None
        )
        if actual is None or types[key] != actual:
            raise ValueError(f"{label} scalar type does not match {key}")

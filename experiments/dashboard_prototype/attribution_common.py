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
class AttributionRowEvidence:
    """One row-local execution output; aggregate experiment evidence never promotes it."""

    experiment_id: AttributionExperimentId
    row_id: str
    producer: str
    native_session_id: str | None
    state: AttributionRowState
    blocked_reason: str | None
    event_id_inputs: Mapping[str, Mapping[str, object]] | None
    event_ids: Mapping[str, str] | None
    direct_remote_sql_parameters: Mapping[str, _ROW_SCALAR] | None
    direct_remote_sql_parameter_types: Mapping[str, str] | None
    direct_remote_result: Mapping[str, _ROW_SCALAR] | None
    direct_remote_result_types: Mapping[str, str] | None
    oracle_parameters: Mapping[str, _ROW_SCALAR] | None
    oracle_parameter_types: Mapping[str, str] | None
    oracle_result: Mapping[str, _ROW_SCALAR] | None
    oracle_result_types: Mapping[str, str] | None

    def __post_init__(self) -> None:
        _validate_row_identity(self)
        if self.state is AttributionRowState.BLOCKED:
            _validate_blocked_row(self)
            return
        _validate_ready_row(self)

    def deterministic_id(self) -> str:
        """Return the immutable identity of this row obligation."""
        return hashlib.sha256(
            json.dumps(
                {
                    "experiment_id": self.experiment_id.value,
                    "row_id": self.row_id,
                    "producer": self.producer,
                    "native_session_id": self.native_session_id,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()


def _validate_row_identity(row: AttributionRowEvidence) -> None:
    if row.row_id not in _ROW_IDS:
        raise ValueError("row evidence must target A07--A09")
    if not isinstance(row.producer, str):
        raise ValueError("row evidence requires a producer")


def _evidence_bindings(row: AttributionRowEvidence) -> tuple[object | None, ...]:
    return (
        row.event_id_inputs,
        row.event_ids,
        row.direct_remote_sql_parameters,
        row.direct_remote_sql_parameter_types,
        row.direct_remote_result,
        row.direct_remote_result_types,
        row.oracle_parameters,
        row.oracle_parameter_types,
        row.oracle_result,
        row.oracle_result_types,
    )


def _validate_blocked_row(row: AttributionRowEvidence) -> None:
    if (
        row.native_session_id is not None
        or not row.blocked_reason
        or any(value is not None for value in _evidence_bindings(row))
    ):
        raise ValueError("blocked rows retain no evidence bundle")


def _validate_ready_row(row: AttributionRowEvidence) -> None:
    if row.blocked_reason is not None:
        raise ValueError("ready rows cannot retain a blocked reason")
    if not isinstance(row.native_session_id, str) or not row.native_session_id:
        raise ValueError("ready row evidence requires exact native lineage")
    if any(value is None for value in _evidence_bindings(row)):
        raise ValueError("ready rows require complete evidence bindings")
    _validate_ready_event_ids(row)
    _validate_row_scalars(
        row.direct_remote_sql_parameters,
        row.direct_remote_sql_parameter_types,
        "direct remote SQL parameters",
    )
    _validate_row_scalars(
        row.direct_remote_result, row.direct_remote_result_types, "direct remote result"
    )
    _validate_row_scalars(row.oracle_parameters, row.oracle_parameter_types, "oracle parameters")
    _validate_row_scalars(row.oracle_result, row.oracle_result_types, "oracle result")
    if row.direct_remote_result != row.oracle_result:
        raise ValueError("row direct remote result must equal its independent oracle")


def _validate_ready_event_ids(row: AttributionRowEvidence) -> None:
    assert row.event_id_inputs is not None
    assert row.event_ids is not None
    assert row.native_session_id is not None
    if set(row.event_id_inputs) != set(_ROW_STAGES) or set(row.event_ids) != set(_ROW_STAGES):
        raise ValueError("row evidence requires source, reducer, and delivery IDs")
    for ordinal, stage in enumerate(_ROW_STAGES):
        _validate_event_id_input(row, stage, ordinal)
        if not isinstance(row.event_ids[stage], str) or not row.event_ids[stage]:
            raise ValueError("row event IDs must be immutable scalar IDs")


def _validate_event_id_input(row: AttributionRowEvidence, stage: str, ordinal: int) -> None:
    assert row.event_id_inputs is not None
    event_input = row.event_id_inputs[stage]
    if set(event_input) != _ROW_EVENT_INPUT_FIELDS:
        raise ValueError("row event IDs require the canonical identity fields")
    if (
        event_input.get("experiment_id") != row.experiment_id.value
        or event_input.get("row_id") != row.row_id
        or event_input.get("producer") != row.producer
        or event_input.get("native_session_id") != row.native_session_id
        or not isinstance(event_input.get("source_id"), str)
        or not event_input["source_id"]
        or not isinstance(event_input.get("reducer_id"), str)
        or not event_input["reducer_id"]
        or event_input.get("entity_version") != 2
        or not isinstance(event_input.get("event_id_ordinal"), int)
        or isinstance(event_input["event_id_ordinal"], bool)
        or event_input["event_id_ordinal"] != ordinal
    ):
        raise ValueError("row event IDs must bind exact native lineage")

    def deterministic_id(self) -> str:
        """Return the immutable identity of this row obligation."""
        return hashlib.sha256(
            json.dumps(
                {
                    "experiment_id": self.experiment_id.value,
                    "row_id": self.row_id,
                    "producer": self.producer,
                    "native_session_id": self.native_session_id,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()


def _validate_row_scalars(
    values: Mapping[str, _ROW_SCALAR] | None,
    types: Mapping[str, str] | None,
    label: str,
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

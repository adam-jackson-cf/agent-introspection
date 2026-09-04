"""Live fail-closed E-Recurrence-3 audit for durable machine rule registries."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Final

from experiments.dashboard_prototype.contracts import EvidenceProvenance, ExperimentResult
from experiments.dashboard_prototype.experiment_live_common import LiveProofRequest
from experiments.dashboard_prototype.recurrence_common import (
    SUPPORTED_SURFACES,
    RecurrenceExperimentId,
    RecurrenceExperimentProof,
    RecurrenceLiveEvidence,
    RecurrencePrimitive,
)

_REGISTRY_SCHEMAS: Final[dict[str, tuple[tuple[str, str], ...]]] = {
    "versioned_rule_registry": (
        ("producer", "TEXT"),
        ("surface", "TEXT"),
        ("native_session_hash", "TEXT"),
        ("rule_id", "TEXT"),
        ("rule_version", "TEXT"),
        ("trigger", "TEXT"),
        ("required_action", "TEXT"),
        ("observability_condition", "TEXT"),
        ("source_time_ns", "INTEGER"),
        ("source_order", "INTEGER"),
        ("evidence_id", "TEXT"),
    ),
    "applicability_registry": (
        ("producer", "TEXT"),
        ("surface", "TEXT"),
        ("native_session_hash", "TEXT"),
        ("rule_id", "TEXT"),
        ("rule_version", "TEXT"),
        ("task_id", "TEXT"),
        ("state", "TEXT"),
        ("source_time_ns", "INTEGER"),
        ("source_order", "INTEGER"),
        ("evidence_id", "TEXT"),
    ),
    "violation_registry": (
        ("producer", "TEXT"),
        ("surface", "TEXT"),
        ("native_session_hash", "TEXT"),
        ("rule_id", "TEXT"),
        ("rule_version", "TEXT"),
        ("task_id", "TEXT"),
        ("state", "TEXT"),
        ("observed_action", "TEXT"),
        ("authoritative", "INTEGER"),
        ("source_time_ns", "INTEGER"),
        ("source_order", "INTEGER"),
        ("evidence_id", "TEXT"),
    ),
}


def extract(connection: sqlite3.Connection, request: LiveProofRequest) -> RecurrenceLiveEvidence:
    """Audit durable registry schemas without promoting unextracted rows to authority."""
    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("live rule audit requires a sqlite3 connection")
    audits = _audit_registries(connection)
    primitives = tuple(
        primitive
        for producer_ordinal in range(len(SUPPORTED_SURFACES))
        for primitive in _producer_primitives(connection, request, producer_ordinal, audits)
    )
    proof = RecurrenceExperimentProof(
        experiment_id=RecurrenceExperimentId.RULE_ADHERENCE,
        run_id=request.run_id,
        result=ExperimentResult.BLOCKED,
        provenance=EvidenceProvenance.FRESH_REAL,
        source_start=request.start,
        source_end=request.end,
        source_boundary=request.source_boundary,
        metrics={
            "registry_schema_present_count": sum(
                state == "schema_present" for state in audits.values()
            ),
            "registry_schema_gap_count": sum(
                state != "schema_present" for state in audits.values()
            ),
        },
        assertions={
            "durable_machine_registry_present": all(
                state == "schema_present" for state in audits.values()
            ),
            "registry_schema_complete": all(state == "schema_present" for state in audits.values()),
            "registry_authority_extracted": False,
            "static_guidance_is_not_authority": True,
            "detector_findings_are_not_authority": True,
        },
        evidence_ids=(),
        blocked_boundaries=("missing_durable_authority",),
        proposal="add_durable_machine_rule_applicability_violation_registry",
    )
    return RecurrenceLiveEvidence(proof=proof, primitives=primitives, remote_calculation_id=None)


def _audit_registries(connection: sqlite3.Connection) -> dict[str, str]:
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    return {
        table: _schema_state(connection, table, schema, tables)
        for table, schema in _REGISTRY_SCHEMAS.items()
    }


def _schema_state(
    connection: sqlite3.Connection,
    table: str,
    schema: tuple[tuple[str, str], ...],
    tables: set[str],
) -> str:
    if table not in tables:
        return "schema_absent"
    columns = tuple(
        (str(row[1]), str(row[2]).upper())
        for row in connection.execute(f'PRAGMA table_info("{table}")')
    )
    return "schema_present" if columns == schema else "schema_invalid"


def _producer_primitives(
    connection: sqlite3.Connection,
    request: LiveProofRequest,
    producer_ordinal: int,
    audits: Mapping[str, str],
) -> tuple[RecurrencePrimitive, ...]:
    producer, surface = SUPPORTED_SURFACES[producer_ordinal]
    ordinal_offset = producer_ordinal * 4
    registry_primitives = tuple(
        RecurrencePrimitive(
            experiment_id=RecurrenceExperimentId.RULE_ADHERENCE,
            source_time=request.end,
            ordinal=ordinal_offset + ordinal,
            dimensions={
                "producer": producer,
                "surface": surface,
                "stable_identity": registry,
                "capability_state": state,
            },
            measures={
                "reducer_counts": (
                    _registry_count(connection, registry, producer, surface)
                    if state == "schema_present"
                    else 1
                )
            },
        )
        for ordinal, (registry, state) in enumerate(audits.items())
    )
    authority_gap_count = sum(state != "schema_present" for state in audits.values())
    gap_state = (
        "schema_present"
        if authority_gap_count == 0
        else "schema_invalid"
        if any(state == "schema_invalid" for state in audits.values())
        else "schema_absent"
    )
    return (
        *registry_primitives,
        RecurrencePrimitive(
            experiment_id=RecurrenceExperimentId.RULE_ADHERENCE,
            source_time=request.end,
            ordinal=ordinal_offset + len(registry_primitives),
            dimensions={
                "producer": producer,
                "surface": surface,
                "stable_identity": "authority_gap_count",
                "capability_state": gap_state,
            },
            measures={"reducer_counts": authority_gap_count},
        ),
    )


def _registry_count(connection: sqlite3.Connection, table: str, producer: str, surface: str) -> int:
    row = connection.execute(
        f'SELECT COUNT(*) FROM "{table}" WHERE producer = ? AND surface = ?',
        (producer, surface),
    ).fetchone()
    return int(row[0]) if row is not None else 0

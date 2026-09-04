"""Fail-closed live E-Recurrence-1 audit of immutable finding authority."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from typing import Final

from experiments.dashboard_prototype.contracts import EvidenceProvenance, ExperimentResult
from experiments.dashboard_prototype.experiment_live_common import LiveProofRequest
from experiments.dashboard_prototype.recurrence_common import (
    RecurrenceExperimentId,
    RecurrenceExperimentProof,
    RecurrenceLiveEvidence,
    RecurrencePrimitive,
)

_REQUIRED_COLUMNS: Final[dict[str, frozenset[str]]] = {
    "findings": frozenset(
        {
            "id",
            "fingerprint",
            "detector_id",
            "detector_version",
            "entity_version",
            "is_active",
            "replaced_by_finding_id",
            "evidence_start_ns",
            "evidence_end_ns",
            "first_seen_ns",
            "last_seen_ns",
            "occurrence_count",
            "canonical_task_count",
            "local_day_count",
        }
    ),
    "canonical_finding_membership": frozenset(
        {
            "finding_id",
            "activity_id",
            "activity_version",
            "canonical_task_id",
            "rationale",
            "created_at",
        }
    ),
    "canonical_activities": frozenset(
        {
            "id",
            "producer",
            "producer_surface",
            "canonical_task_id",
            "source_ended_at_ns",
            "source_membership_hash",
            "source_membership_json",
        }
    ),
    "canonical_activity_versions": frozenset(
        {"activity_id", "version", "attribution_state", "project_identity_id"}
    ),
}

_AUTHORITY_GAPS: Final[frozenset[str]] = frozenset(
    {"missing_evidence_window", "missing_selected_membership"}
)


def extract(connection: sqlite3.Connection, request: LiveProofRequest) -> RecurrenceLiveEvidence:
    """Read only bounded, durable finding inputs; never manufacture authority."""
    tables = _table_columns(connection)
    schema_supported = all(
        tables.get(name, frozenset()) >= columns for name, columns in _REQUIRED_COLUMNS.items()
    )
    counts = _population_counts(connection, schema_supported, request)
    contradictions = _contradictions(connection, schema_supported, request)
    finding_bounds_authoritative = (
        schema_supported
        and not {"missing_evidence_window", "invalid_evidence_window"} & contradictions
    )
    membership_authoritative = (
        schema_supported
        and not {
            "orphan_membership",
            "canonical_task_membership_mismatch",
            "membership_not_latest_activity_version",
            "duplicate_member",
            "missing_selected_membership",
        }
        & contradictions
    )
    reconciled = schema_supported and not contradictions
    result = (
        ExperimentResult.FAILED if contradictions - _AUTHORITY_GAPS else ExperimentResult.BLOCKED
    )
    finding_version_source_ids, canonical_task_membership_source_ids = (
        _source_ids(connection, request) if reconciled else ((), ())
    )
    evidence_ids = tuple(
        sorted((*finding_version_source_ids, *canonical_task_membership_source_ids))
    )
    assertions = {
        "schema_required": schema_supported,
        "durable_window_bounds_authoritative": finding_bounds_authoritative,
        "membership_task_identity_authoritative": membership_authoritative,
        "selected_finding_evidence_range_exact": finding_bounds_authoritative,
        "selected_membership_denominator_reconciled": reconciled,
        "latest_activity_versions_global": schema_supported
        and "ambiguous_latest_activity_version" not in contradictions,
        "duplicate_fingerprint_identity_absent": "duplicate_fingerprint" not in contradictions,
        "duplicate_member_identity_absent": "duplicate_member" not in contradictions,
        "membership_references_valid": "orphan_membership" not in contradictions,
        "lineage_shape_valid": not {
            "supersession_shape",
            "supersession_cycle",
            "supersession_version",
        }
        & contradictions,
    }
    if contradictions:
        assertions["contradiction_detected"] = True
    proof = RecurrenceExperimentProof(
        experiment_id=RecurrenceExperimentId.FINDING_PROJECTION,
        run_id=request.run_id,
        result=result,
        provenance=EvidenceProvenance.FRESH_REAL,
        source_start=request.start.astimezone(UTC),
        source_end=request.end.astimezone(UTC),
        source_boundary=request.source_boundary,
        metrics={**counts, "contradiction_count": len(contradictions)},
        assertions=assertions,
        evidence_ids=evidence_ids,
        blocked_boundaries=("missing_durable_authority",)
        if result is ExperimentResult.BLOCKED
        else (),
        proposal="immutable_finding_projection_required",
    )
    primitives = tuple(
        RecurrencePrimitive(
            experiment_id=RecurrenceExperimentId.FINDING_PROJECTION,
            source_time=request.end.astimezone(UTC),
            ordinal=ordinal,
            dimensions={
                "stable_identity": population,
                "capability_state": "supported" if schema_supported else "missing",
            },
            measures={"reducer_counts": float(count)},
        )
        for ordinal, (population, count) in enumerate(sorted(counts.items()))
    )
    return RecurrenceLiveEvidence(
        proof=proof,
        primitives=primitives,
        remote_calculation_id=None,
        finding_version_source_ids=finding_version_source_ids,
        canonical_task_membership_source_ids=canonical_task_membership_source_ids,
    )


def _table_columns(connection: sqlite3.Connection) -> dict[str, frozenset[str]]:
    names = [
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    ]
    return {
        name: frozenset(str(row[1]) for row in connection.execute(f"PRAGMA table_info({name})"))
        for name in names
    }


def _source_ids(
    connection: sqlite3.Connection, request: LiveProofRequest
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    bounds = (_epoch_ns(request.start), _epoch_ns(request.end))
    finding_rows = tuple(
        connection.execute(
            """
            SELECT id, fingerprint, detector_id, detector_version, entity_version, is_active,
                   replaced_by_finding_id, evidence_start_ns, evidence_end_ns, first_seen_ns,
                   last_seen_ns, occurrence_count, canonical_task_count, local_day_count
            FROM findings
            WHERE evidence_start_ns >= ? AND evidence_end_ns <= ?
            ORDER BY id, entity_version
            """,
            bounds,
        )
    )
    membership_rows = tuple(
        connection.execute(
            """
            SELECT membership.finding_id, membership.activity_id, membership.activity_version,
                   membership.canonical_task_id, membership.rationale, membership.created_at
            FROM canonical_finding_membership AS membership
            JOIN findings ON findings.id = membership.finding_id
            WHERE findings.evidence_start_ns >= ? AND findings.evidence_end_ns <= ?
            ORDER BY membership.finding_id, membership.activity_id, membership.activity_version
            """,
            bounds,
        )
    )
    return (
        tuple(_source_id("finding-version", row) for row in finding_rows),
        tuple(_source_id("canonical-membership", row) for row in membership_rows),
    )


def _source_id(kind: str, row: sqlite3.Row | tuple[object, ...]) -> str:
    payload = json.dumps(tuple(row), ensure_ascii=True, separators=(",", ":"))
    return f"{kind}-{hashlib.sha256(payload.encode()).hexdigest()}"


def _population_counts(
    connection: sqlite3.Connection, schema_supported: bool, request: LiveProofRequest
) -> dict[str, int]:
    if not schema_supported:
        # A missing source has no denominator.  Emit only the fact that authority was audited.
        return {"authority_available": 0}
    start_ns, end_ns = _epoch_ns(request.start), _epoch_ns(request.end)
    return {
        "membership_rows": _count(connection, "SELECT COUNT(*) FROM canonical_finding_membership"),
        "selected_finding_rows": _count(
            connection,
            "SELECT COUNT(*) FROM findings WHERE evidence_start_ns >= ? AND evidence_end_ns <= ?",
            (start_ns, end_ns),
        ),
        "selected_membership_rows": _count(
            connection,
            """SELECT COUNT(*) FROM canonical_finding_membership AS membership
               JOIN findings ON findings.id = membership.finding_id
               WHERE findings.evidence_start_ns >= ? AND findings.evidence_end_ns <= ?""",
            (start_ns, end_ns),
        ),
        "latest_activity_version_rows": _count(
            connection,
            """WITH latest AS (
                   SELECT activity_id, MAX(version) AS version
                   FROM canonical_activity_versions GROUP BY activity_id
               )
               SELECT COUNT(*) FROM canonical_activity_versions AS version
               JOIN latest
                 ON latest.activity_id = version.activity_id
                AND latest.version = version.version""",
        ),
    }


def _contradictions(
    connection: sqlite3.Connection, schema_supported: bool, request: LiveProofRequest
) -> frozenset[str]:
    if not schema_supported:
        return frozenset()
    contradictions: set[str] = set()
    start_ns, end_ns = _epoch_ns(request.start), _epoch_ns(request.end)
    checks = {
        "duplicate_fingerprint": """SELECT COUNT(*) FROM (
            SELECT detector_id, detector_version, fingerprint FROM findings WHERE is_active = 1
            GROUP BY detector_id, detector_version, fingerprint HAVING COUNT(*) > 1)""",
        "duplicate_member": """SELECT COUNT(*) FROM (
            SELECT finding_id, activity_id FROM canonical_finding_membership
            GROUP BY finding_id, activity_id HAVING COUNT(*) > 1)""",
        "orphan_membership": """SELECT COUNT(*) FROM canonical_finding_membership AS membership
            LEFT JOIN findings ON findings.id = membership.finding_id
            LEFT JOIN canonical_activities AS activity ON activity.id = membership.activity_id
            WHERE findings.id IS NULL OR activity.id IS NULL""",
        "canonical_task_membership_mismatch": """SELECT COUNT(*)
            FROM canonical_finding_membership AS membership
            JOIN canonical_activities AS activity ON activity.id = membership.activity_id
            WHERE membership.canonical_task_id != activity.canonical_task_id""",
        "missing_evidence_window": """SELECT COUNT(*) FROM findings
            WHERE evidence_start_ns IS NULL OR evidence_end_ns IS NULL""",
        "invalid_evidence_window": """SELECT COUNT(*) FROM findings
            WHERE evidence_start_ns IS NOT NULL AND evidence_end_ns IS NOT NULL
              AND (evidence_start_ns >= evidence_end_ns
                   OR first_seen_ns < evidence_start_ns OR last_seen_ns > evidence_end_ns
                   OR first_seen_ns > last_seen_ns)""",
        "missing_selected_membership": """SELECT COUNT(*) FROM (
            SELECT finding.id FROM findings AS finding
            LEFT JOIN canonical_finding_membership AS membership
              ON membership.finding_id = finding.id
            WHERE finding.evidence_start_ns >= ? AND finding.evidence_end_ns <= ?
            GROUP BY finding.id
            HAVING COUNT(membership.activity_id) = 0)""",
        "selected_denominator": """SELECT COUNT(*) FROM (
            SELECT finding.id FROM findings AS finding
            LEFT JOIN canonical_finding_membership AS membership
              ON membership.finding_id = finding.id
            WHERE finding.evidence_start_ns >= ? AND finding.evidence_end_ns <= ?
            GROUP BY finding.id
            HAVING COUNT(membership.activity_id) > 0
               AND (finding.occurrence_count != COUNT(membership.activity_id)
                    OR finding.canonical_task_count
                       != COUNT(DISTINCT membership.canonical_task_id)))""",
        "membership_not_latest_activity_version": """SELECT COUNT(*)
            FROM canonical_finding_membership AS membership
            JOIN canonical_activity_versions AS version
              ON version.activity_id = membership.activity_id
             AND version.version = membership.activity_version
            JOIN (
                SELECT activity_id, MAX(version) AS version
                FROM canonical_activity_versions GROUP BY activity_id
            ) AS latest
              ON latest.activity_id = membership.activity_id
            WHERE membership.activity_version != latest.version""",
        "supersession_shape": """SELECT COUNT(*) FROM findings AS child
            LEFT JOIN findings AS parent ON parent.id = child.replaced_by_finding_id
            WHERE (child.is_active = 1 AND child.replaced_by_finding_id IS NOT NULL)
               OR (
                   child.is_active = 0
                   AND (child.replaced_by_finding_id IS NULL OR parent.id IS NULL)
               )""",
        "supersession_version": """SELECT COUNT(*) FROM findings AS child
            JOIN findings AS parent ON parent.id = child.replaced_by_finding_id
            WHERE child.entity_version >= parent.entity_version
               OR child.detector_id != parent.detector_id
               OR child.detector_version != parent.detector_version
               OR child.fingerprint != parent.fingerprint""",
        "ambiguous_latest_activity_version": """WITH latest AS (
              SELECT activity_id, MAX(version) AS version
              FROM canonical_activity_versions
              GROUP BY activity_id
            ) SELECT COUNT(*) FROM (
              SELECT version.activity_id
              FROM canonical_activity_versions AS version
              JOIN latest
                ON latest.activity_id = version.activity_id
               AND latest.version = version.version
              GROUP BY version.activity_id
              HAVING COUNT(*) > 1)""",
    }
    for name, query in checks.items():
        parameters = (
            (start_ns, end_ns)
            if name in {"missing_selected_membership", "selected_denominator"}
            else ()
        )
        if _count(connection, query, parameters):
            contradictions.add(name)
    if _count(
        connection,
        """WITH RECURSIVE chain(root_id, finding_id, path, cycle) AS (
             SELECT id, replaced_by_finding_id, ',' || id || ',', 0
             FROM findings
             WHERE is_active = 0
             UNION ALL
             SELECT chain.root_id, finding.replaced_by_finding_id,
                    chain.path || finding.id || ',',
                    instr(chain.path, ',' || finding.id || ',') > 0
             FROM chain
             JOIN findings AS finding ON finding.id = chain.finding_id
             WHERE chain.cycle = 0
           ) SELECT COUNT(*) FROM chain WHERE cycle = 1""",
    ):
        contradictions.add("supersession_cycle")
    return frozenset(contradictions)


def _epoch_ns(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("finding request bounds must be timezone-aware")
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value.astimezone(UTC) - epoch
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


def _count(connection: sqlite3.Connection, query: str, parameters: tuple[int, ...] = ()) -> int:
    return int(connection.execute(query, parameters).fetchone()[0])

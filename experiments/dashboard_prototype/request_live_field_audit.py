"""Live E-Request-1 extraction from the durable session-context boundary."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC
from typing import Final

from experiments.dashboard_prototype.contracts import EvidenceProvenance
from experiments.dashboard_prototype.experiment_live_common import (
    LiveProofRequest,
    RemoteCalculationPrimitive,
)
from experiments.dashboard_prototype.request_common import (
    RequestExperimentId,
    RequestLiveEvidence,
)
from experiments.dashboard_prototype.request_field_audit import (
    PRODUCER_SURFACES,
    FieldAuthorityState,
    RequestFieldClassification,
    build_proof,
    classification_counts,
    current_classifications,
    validate_classifications,
)

_REQUIRED_CONTEXT_COLUMNS: Final[frozenset[str]] = frozenset(
    {"producer", "session_id", "occurred_at", "project_id"}
)
_COMMON_FIELDS: Final[frozenset[str]] = frozenset(
    {"producer", "surface", "native_session_id", "project_id"}
)


def extract(connection: sqlite3.Connection, request: LiveProofRequest) -> RequestLiveEvidence:
    """Extract E1 without reading payloads or treating a session as a request."""
    classifications = _live_classifications(connection, request)
    proof = build_proof(
        request.run_id,
        classifications,
        source_boundary=request.source_boundary,
        provenance=EvidenceProvenance.FRESH_REAL,
    )
    primitives = tuple(
        RemoteCalculationPrimitive(
            experiment_id=RequestExperimentId.FIELD_AUDIT,
            source_time=request.end.astimezone(UTC),
            ordinal=ordinal,
            dimensions={
                "producer": row.producer,
                "surface": row.surface,
                "field": row.field,
                "authority_state": row.state.value,
            },
            measures={"classification_count": 1},
        )
        for ordinal, row in enumerate(classifications)
    )
    counts = classification_counts(classifications)
    return RequestLiveEvidence(
        proof=proof,
        primitives=primitives,
        remote_query_id=None,
        remote_oracle={
            state.value: {"classification_count": count} for state, count in counts.items()
        },
    )


def _live_classifications(
    connection: sqlite3.Connection, request: LiveProofRequest
) -> tuple[RequestFieldClassification, ...]:
    columns = _context_columns(connection)
    observed = (
        _observed_producers(connection, request)
        if columns >= _REQUIRED_CONTEXT_COLUMNS
        else frozenset()
    )
    base = current_classifications()
    return validate_classifications(_reclassify(row, columns, observed) for row in base)


def _reclassify(
    row: RequestFieldClassification,
    columns: frozenset[str],
    observed: frozenset[str],
) -> RequestFieldClassification:
    if row.field not in _COMMON_FIELDS:
        return row
    if not columns >= _REQUIRED_CONTEXT_COLUMNS:
        state = FieldAuthorityState.UNSUPPORTED
    elif row.producer not in observed:
        # No in-window row establishes neither a zero population nor unsupported authority.
        state = FieldAuthorityState.AMBIGUOUS
    else:
        state = FieldAuthorityState.AUTHORITATIVE
    return RequestFieldClassification(row.producer, row.surface, row.field, state)


def _context_columns(connection: sqlite3.Connection) -> frozenset[str]:
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if "session_context_events" not in tables:
        return frozenset()
    return frozenset(
        str(row[1]) for row in connection.execute("PRAGMA table_info(session_context_events)")
    )


def _observed_producers(
    connection: sqlite3.Connection, request: LiveProofRequest
) -> frozenset[str]:
    allowed = tuple(producer for producer, _ in PRODUCER_SURFACES)
    placeholders = ",".join("?" for _ in allowed)
    start = request.start.astimezone(UTC).isoformat()
    end = request.end.astimezone(UTC).isoformat()
    rows: Iterable[tuple[str]] = connection.execute(
        "SELECT DISTINCT producer FROM session_context_events "
        f"WHERE producer IN ({placeholders}) "
        "AND occurred_at > ? AND occurred_at <= ?",
        (*allowed, start, end),
    )
    return frozenset(str(row[0]) for row in rows)

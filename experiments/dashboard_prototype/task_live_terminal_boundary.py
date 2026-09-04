"""Fresh-real E-Task-3 extraction that audits context without inferring outcomes."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC
from typing import Final

from agent_introspection.session_context import SessionContextError, parse_event
from experiments.dashboard_prototype.contracts import EvidenceProvenance
from experiments.dashboard_prototype.experiment_live_common import LiveProofRequest
from experiments.dashboard_prototype.task_common import SUPPORTED_SURFACES, TaskLiveEvidence
from experiments.dashboard_prototype.task_terminal_boundary import build_proof, current_audits

_REQUIRED_CONTEXT_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "event_id",
        "producer",
        "session_id",
        "event_type",
        "occurred_at",
        "project_id",
        "project_name",
        "project_root",
        "project_kind",
    }
)


def extract(connection: sqlite3.Connection, request: LiveProofRequest) -> TaskLiveEvidence:
    """Audit authoritative context only; lifecycle or content never becomes a task outcome."""
    columns = context_columns(connection)
    source_authority_present = columns >= _REQUIRED_CONTEXT_COLUMNS and valid_source_rows(
        connection, request
    )
    observed = observed_producers(connection, request) if source_authority_present else frozenset()
    proof = build_proof(
        request.run_id,
        current_audits(),
        source_boundary=request.source_boundary,
        provenance=(
            EvidenceProvenance.FRESH_REAL
            if source_authority_present
            else EvidenceProvenance.RETAINED
        ),
        source_authority_present=source_authority_present,
    )
    return TaskLiveEvidence(
        proof=proof,
        primitives=(),
        remote_query_id=None,
        remote_oracle={
            "terminal-boundary": {
                "context_schema_present": int(columns >= _REQUIRED_CONTEXT_COLUMNS),
                "source_authority_present": int(source_authority_present),
                "observed_producer_population": len(observed),
                "terminal_candidate_population": 0,
            }
        },
    )


def context_columns(connection: sqlite3.Connection) -> frozenset[str]:
    """Return only context column names, not context values or payloads."""
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if "session_context_events" not in tables:
        return frozenset()
    return frozenset(
        str(row[1]) for row in connection.execute("PRAGMA table_info(session_context_events)")
    )


def valid_source_rows(connection: sqlite3.Connection, request: LiveProofRequest) -> bool:
    """Validate every source row with the public canonical event parser."""
    producers = tuple(producer for producer, _ in SUPPORTED_SURFACES)
    placeholders = ",".join("?" for _ in producers)
    rows: Iterable[tuple[str, str, str, str, str, str, str, str, str]] = connection.execute(
        "SELECT event_id, producer, session_id, event_type, occurred_at, project_id, "
        "project_name, project_root, project_kind FROM session_context_events "
        f"WHERE producer IN ({placeholders})",
        producers,
    )
    try:
        events = tuple(parse_event(_canonical_event(row)) for row in rows)
    except SessionContextError:
        return False
    start = request.start.astimezone(UTC)
    end = request.end.astimezone(UTC)
    return any(start < event.occurred_at <= end for event in events)


def observed_producers(connection: sqlite3.Connection, request: LiveProofRequest) -> frozenset[str]:
    """Return canonical producers with validated context rows in the exact UTC interval."""
    producers = tuple(producer for producer, _ in SUPPORTED_SURFACES)
    placeholders = ",".join("?" for _ in producers)
    rows: Iterable[tuple[str]] = connection.execute(
        "SELECT DISTINCT producer FROM session_context_events "
        f"WHERE producer IN ({placeholders}) "
        "AND occurred_at > ? AND occurred_at <= ?",
        (*producers, *_utc_bounds(request)),
    )
    return frozenset(str(row[0]) for row in rows)


def _canonical_event(
    row: tuple[str, str, str, str, str, str, str, str, str],
) -> dict[str, object]:
    event_id, producer, session_id, event_type, occurred_at, project_id, name, root, kind = row
    return {
        "event_id": event_id,
        "producer": producer,
        "session_id": session_id,
        "event_type": event_type,
        "occurred_at": occurred_at,
        "agent": {"project": {"id": project_id, "name": name, "root": root, "kind": kind}},
    }


def _utc_bounds(request: LiveProofRequest) -> tuple[str, str]:
    return (request.start.astimezone(UTC).isoformat(), request.end.astimezone(UTC).isoformat())

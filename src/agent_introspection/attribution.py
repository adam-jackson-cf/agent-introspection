"""Evidence-only project attribution for derived agent observations."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass

from agent_introspection.identities import ProjectIdentity, discover_project
from agent_introspection.source import TraceRow

ATTRIBUTION_CONTRACT_VERSION = 1


@dataclass(frozen=True, slots=True)
class Attribution:
    """The project identity and exact source-backed method, if any."""

    project_id: str | None
    method: str
    project: ProjectIdentity | None = None


def _evidence_id(
    *,
    thread_id: str,
    trace_id: str,
    timestamp_ns: int,
    source_contract_fingerprint: str,
    project_id: str,
) -> str:
    fields = (
        thread_id,
        trace_id,
        str(timestamp_ns),
        source_contract_fingerprint,
        str(ATTRIBUTION_CONTRACT_VERSION),
        project_id,
    )
    return hashlib.sha256("\x1f".join(fields).encode()).hexdigest()


def direct_trace_attribution(trace: TraceRow | None) -> Attribution:
    """Resolve only a locally validated, explicit trace cwd."""

    if trace is None or trace.cwd is None:
        return Attribution(None, "unresolved")
    try:
        project = discover_project(trace.cwd)
    except (OSError, ValueError):
        return Attribution(None, "unresolved")
    return Attribution(project.identity, "trace_cwd", project)


def persist_thread_evidence(
    connection: sqlite3.Connection,
    *,
    trace: TraceRow,
    attribution: Attribution,
    source_contract_fingerprint: str,
    created_at: str,
) -> None:
    """Persist append-only provenance for one direct, validated trace cwd."""

    if (
        attribution.method != "trace_cwd"
        or attribution.project_id is None
        or trace.thread_id is None
    ):
        return
    timestamp_ns = int(trace.ended_at.timestamp() * 1_000_000_000)
    connection.execute(
        """
        INSERT INTO thread_project_evidence (
            id, thread_id, source_trace_id, source_timestamp_ns,
            source_contract_fingerprint, attribution_contract_version,
            project_identity_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO NOTHING
        """,
        (
            _evidence_id(
                thread_id=trace.thread_id,
                trace_id=trace.trace_id,
                timestamp_ns=timestamp_ns,
                source_contract_fingerprint=source_contract_fingerprint,
                project_id=attribution.project_id,
            ),
            trace.thread_id,
            trace.trace_id,
            timestamp_ns,
            source_contract_fingerprint,
            ATTRIBUTION_CONTRACT_VERSION,
            attribution.project_id,
            created_at,
        ),
    )


def _unique_thread_project(
    connection: sqlite3.Connection, *, thread_id: str, start_ns: int, end_ns: int
) -> str | None:
    rows = connection.execute(
        """
        SELECT DISTINCT project_identity_id
        FROM thread_project_evidence
        WHERE thread_id = ? AND source_timestamp_ns >= ? AND source_timestamp_ns <= ?
        ORDER BY project_identity_id
        """,
        (thread_id, start_ns, end_ns),
    ).fetchall()
    return str(rows[0][0]) if len(rows) == 1 else None


def resolve_attribution(
    connection: sqlite3.Connection,
    *,
    trace: TraceRow | None,
    thread_id: str | None,
    conversation_thread_id: str | None,
    start_ns: int,
    end_ns: int,
) -> Attribution:
    """Apply the canonical direct > thread > conversation-thread precedence."""

    direct = direct_trace_attribution(trace)
    if direct.project_id is not None:
        return direct
    if thread_id is not None:
        project_id = _unique_thread_project(
            connection, thread_id=thread_id, start_ns=start_ns, end_ns=end_ns
        )
        if project_id is not None:
            return Attribution(project_id, "thread_cwd")
    if conversation_thread_id is not None:
        project_id = _unique_thread_project(
            connection, thread_id=conversation_thread_id, start_ns=start_ns, end_ns=end_ns
        )
        if project_id is not None:
            return Attribution(project_id, "conversation_thread_cwd")
    return Attribution(None, "unresolved")

"""Explicit, bounded manual Codex workspace attribution."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agent_introspection.attribution import canonical_activity_event_attributes
from agent_introspection.config import AppConfig
from agent_introspection.database import (
    CanonicalActivity,
    CanonicalAttribution,
    CanonicalSourceMembership,
    persist_canonical_activity,
)
from agent_introspection.identities import IdentityError, canonical_git_project, normalize_target
from agent_introspection.normalization import NormalizationError, parse_tool_arguments
from agent_introspection.source import ClickHouseClient
from agent_introspection.telemetry import (
    CANONICAL_ACTIVITY_EVENT_NAME,
    CanonicalActivityVersionEvent,
    RemoteEventReference,
    drain_outbox_event_ids,
    enqueue_canonical_activity_version,
    remote_event_ids,
)

MAXIMUM_RANGE_HELP = (
    "Maximum supported manual range is configured by "
    "legacy_project_attribution.maximum_range_hours."
)
_DETECTOR_ID = "legacy_project_attribution"
_ALLOWED_TOOL_NAMES = frozenset({"exec"})
_ALLOWED_ARGUMENT_KEYS = frozenset({"cmd", "workdir", "yield_time_ms", "max_output_chars"})

LEGACY_PROJECT_ATTRIBUTION_QUERY = r"""
SELECT
    timestamp,
    id AS log_id,
    multiIf(
      notEmpty(attributes_string['thread.id'])
        AND notEmpty(attributes_string['thread_id'])
        AND attributes_string['thread.id'] != attributes_string['thread_id'],
      '',
      notEmpty(attributes_string['thread.id']), attributes_string['thread.id'],
      attributes_string['thread_id']
    ) AS correlation_id,
    attributes_string['call_id'] AS call_id,
    attributes_string['tool_name'] AS tool_name,
    attributes_string['arguments'] AS arguments
FROM signoz_logs.distributed_logs_v2
WHERE timestamp >= {start_ns:UInt64}
  AND timestamp < {end_ns:UInt64}
  AND resource.`service.name`::String IN ('codex_exec', 'codex_cli_rs')
  AND attributes_string['event.name'] = 'codex.tool_result'
  AND attributes_string['tool_name'] = 'exec'
  AND notEmpty(attributes_string['call_id'])
ORDER BY timestamp, id
""".strip()


@dataclass(frozen=True, slots=True)
class _Candidate:
    log_id: str
    correlation_id: str
    timestamp_ns: int
    call_id: str
    tool_name: str
    workspace: Path
    target: str


@dataclass(frozen=True, slots=True)
class LegacyProjectAttributionRequest:
    """Inputs defining one explicit bounded legacy attribution run."""

    config: AppConfig
    start: datetime
    end: datetime
    approved_by: str
    delivery_endpoint: str = "http://localhost:4318/v1/logs"


@dataclass(frozen=True, slots=True)
class _CandidateCollection:
    candidates: tuple[_Candidate, ...]
    source_ids: tuple[str, ...]
    denominator: int
    rejected: int
    unresolved: int


@dataclass(frozen=True, slots=True)
class _PersistedActivities:
    activity_ids: tuple[str, ...]
    outbox_ids: tuple[str, ...]
    remote_references: tuple[RemoteEventReference, ...]


@dataclass(frozen=True, slots=True)
class _DeliveryVerificationRequest:
    client: ClickHouseClient
    attribution_request: LegacyProjectAttributionRequest
    fact_set_id: str
    persisted: _PersistedActivities


def _timestamp_ns(value: datetime) -> int:
    utc = value.astimezone(UTC)
    return int(utc.timestamp() * 1_000_000_000)


def parse_rfc3339(value: str) -> datetime:
    """Parse one explicit, timezone-aware RFC3339 bound."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("time must be RFC3339") from exc
    if parsed.tzinfo is None:
        raise ValueError("time must include a timezone offset")
    return parsed.astimezone(UTC)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _candidate_arguments(row: Mapping[str, Any]) -> Mapping[str, object] | None:
    raw_arguments = row.get("arguments")
    if not isinstance(raw_arguments, str):
        return None
    try:
        arguments = parse_tool_arguments(raw_arguments)
    except NormalizationError:
        return None
    if not isinstance(arguments, Mapping) or set(arguments) - _ALLOWED_ARGUMENT_KEYS:
        return None
    return arguments


def _parse_candidate(row: Mapping[str, Any]) -> _Candidate | None:
    log_id = _text(row.get("log_id"))
    correlation_id = _text(row.get("correlation_id"))
    call_id = _text(row.get("call_id"))
    tool_name = _text(row.get("tool_name"))
    timestamp = row.get("timestamp")
    if (
        log_id is None
        or correlation_id is None
        or call_id is None
        or tool_name not in _ALLOWED_TOOL_NAMES
    ):
        return None
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
        return None
    arguments = _candidate_arguments(row)
    if arguments is None:
        return None
    command = arguments.get("cmd")
    workdir = arguments.get("workdir")
    if not isinstance(command, str) or not command or not isinstance(workdir, str) or not workdir:
        return None
    return _Candidate(
        log_id,
        correlation_id,
        timestamp,
        call_id,
        tool_name,
        Path(workdir),
        ".",
    )


def _git_root(workspace: Path) -> Path | None:
    try:
        resolved = workspace.resolve(strict=True)
    except OSError:
        return None
    if not resolved.is_dir():
        return None
    completed = subprocess.run(
        ("git", "-C", str(resolved), "rev-parse", "--show-toplevel"),
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    if completed.returncode != 0:
        return None
    root = Path(completed.stdout.strip()).resolve(strict=False)
    return root if resolved.is_relative_to(root) else None


def _inside_allowed_root(workspace: Path, roots: tuple[Path, ...]) -> bool:
    return any(workspace.is_relative_to(root) for root in roots)


def _project_identity(connection: sqlite3.Connection, project: Any) -> None:
    connection.execute(
        """
        INSERT INTO project_identities (
            id, identity_kind, canonical_path, git_common_dir, canonical_name, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO NOTHING
        """,
        (
            project.identity,
            project.kind,
            project.root.as_posix(),
            (project.root / ".git").as_posix(),
            project.display_name,
            datetime.now(UTC).isoformat(),
        ),
    )


def _fact_set_identity(*, start: datetime, end: datetime, source_ids: Iterable[str]) -> str:
    material = json.dumps(
        {
            "start": start.astimezone(UTC).isoformat(),
            "end": end.astimezone(UTC).isoformat(),
            "source_ids": sorted(source_ids),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _event_set_fingerprint(event_ids: Iterable[str]) -> tuple[list[str], str]:
    ids = sorted(event_ids)
    if len(ids) != len(set(ids)):
        raise ValueError("legacy delivery event IDs must be unique")
    encoded = json.dumps(ids, separators=(",", ":"))
    return ids, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _append_delivery_attempt(
    connection: sqlite3.Connection,
    *,
    fact_set_id: str,
    intended_ids: Iterable[str],
    delivery: dict[str, int],
    remote_ids: Iterable[str],
) -> dict[str, Any]:
    intended, intended_hash = _event_set_fingerprint(intended_ids)
    observed, observed_hash = _event_set_fingerprint(remote_ids)
    verified = set(observed) == set(intended)
    failure_reason = None
    if not verified:
        failure_reason = (
            "local_delivery_incomplete"
            if delivery["pending"] != 0 or delivery["delivered"] != len(intended)
            else "remote_event_id_mismatch"
        )
    verified_at = datetime.now(UTC).isoformat() if verified else None
    with connection:
        connection.execute(
            """
            INSERT INTO legacy_attribution_delivery_attempts(
                fact_set_id, attempted_at, intended_event_ids_json, intended_event_count,
                intended_event_hash, local_delivery_result_json, remote_event_ids_json,
                remote_event_count, remote_event_hash, failure_reason, verified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fact_set_id,
                datetime.now(UTC).isoformat(),
                json.dumps(intended, separators=(",", ":")),
                len(intended),
                intended_hash,
                json.dumps(delivery, sort_keys=True, separators=(",", ":")),
                json.dumps(observed, separators=(",", ":")),
                len(observed),
                observed_hash,
                failure_reason,
                verified_at,
            ),
        )
    return {
        "verified": verified,
        "failure_reason": failure_reason,
        "intended_event_count": len(intended),
        "intended_event_hash": intended_hash,
        "remote_event_count": len(observed),
        "remote_event_hash": observed_hash,
        "verified_at": verified_at,
    }


def _remote_references(
    connection: sqlite3.Connection, event_ids: Iterable[str]
) -> list[RemoteEventReference]:
    ids, _ = _event_set_fingerprint(event_ids)
    if not ids:
        return []
    placeholders = ", ".join("?" for _ in ids)
    rows = connection.execute(
        f"SELECT event_id, payload_json FROM otlp_outbox WHERE event_id IN ({placeholders})",
        ids,
    ).fetchall()
    if len(rows) != len(ids):
        raise RuntimeError(
            "legacy fact set immutable evidence event IDs are not an exact local set"
        )
    references = []
    for event_id, payload_json in rows:
        payload = json.loads(str(payload_json))
        references.append(
            RemoteEventReference(
                event_id=str(event_id),
                event_name=str(payload["event.name"]),
                timestamp_ns=int(payload["timestamp_ns"]),
            )
        )
    return references


def recover_legacy_project_attribution(
    connection: sqlite3.Connection,
    *,
    client: ClickHouseClient,
    fact_set_id: str,
    delivery_endpoint: str = "http://localhost:4318/v1/logs",
) -> dict[str, Any]:
    """Redeliver and verify the immutable event set of one existing legacy fact set."""
    row = connection.execute(
        """
        SELECT intended_event_ids_json, verified_at
        FROM legacy_attribution_delivery_attempts
        WHERE fact_set_id = ?
        ORDER BY id DESC LIMIT 1
        """,
        (fact_set_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"legacy fact set {fact_set_id} has no delivery evidence")
    intended_ids = json.loads(str(row[0]))
    if row[1] is not None:
        return {"status": "verified", "fact_set_id": fact_set_id, "idempotent": True}
    references = _remote_references(connection, intended_ids)
    delivery = (
        drain_outbox_event_ids(
            connection, intended_ids, endpoint=delivery_endpoint, include_delivered=True
        )
        if intended_ids
        else {"selected": 0, "delivered": 0, "pending": 0}
    )
    remote_ids = remote_event_ids(client, references) if references else set()
    result = _append_delivery_attempt(
        connection,
        fact_set_id=fact_set_id,
        intended_ids=intended_ids,
        delivery=delivery,
        remote_ids=remote_ids,
    )
    if not result["verified"]:
        raise RuntimeError(
            f"legacy fact set {fact_set_id} recovery verification failed: "
            f"{result['failure_reason']}"
        )
    return {"status": "verified", "fact_set_id": fact_set_id, "idempotent": False, **result}


def _validate_attribution_request(request: LegacyProjectAttributionRequest) -> None:
    if request.start.tzinfo is None or request.end.tzinfo is None or request.start >= request.end:
        raise ValueError("start and end must be ordered, timezone-aware datetimes")
    if not request.approved_by.strip():
        raise ValueError("approved_by must be non-empty")
    maximum = timedelta(hours=request.config.legacy_project_attribution.maximum_range_hours)
    if request.end - request.start > maximum:
        raise ValueError(MAXIMUM_RANGE_HELP)
    if not request.config.legacy_project_attribution.project_roots:
        raise ValueError("legacy_project_attribution.project_roots must be explicitly configured")


def _collect_candidates(
    client: ClickHouseClient, request: LegacyProjectAttributionRequest
) -> _CandidateCollection:
    candidates: list[_Candidate] = []
    source_ids: list[str] = []
    rejected = 0
    unresolved = 0
    roots = request.config.legacy_project_attribution.project_roots
    for denominator, row in enumerate(
        client.query(
            LEGACY_PROJECT_ATTRIBUTION_QUERY,
            {
                "start_ns": _timestamp_ns(request.start),
                "end_ns": _timestamp_ns(request.end),
            },
        ),
        start=1,
    ):
        source_ids.append(_text(row.get("log_id")) or f"missing:{denominator}")
        candidate = _parse_candidate(row)
        if candidate is None:
            rejected += 1
            continue
        workspace = _git_root(candidate.workspace)
        if workspace is None or not _inside_allowed_root(workspace, roots):
            rejected += 1
            continue
        try:
            target = normalize_target(candidate.target, project_root=workspace)
        except IdentityError:
            unresolved += 1
            continue
        candidates.append(
            _Candidate(
                candidate.log_id,
                candidate.correlation_id,
                candidate.timestamp_ns,
                candidate.call_id,
                candidate.tool_name,
                workspace,
                target,
            )
        )
    return _CandidateCollection(
        candidates=tuple(candidates),
        source_ids=tuple(source_ids),
        denominator=len(source_ids),
        rejected=rejected,
        unresolved=unresolved,
    )


def _record_fact_set(
    connection: sqlite3.Connection,
    request: LegacyProjectAttributionRequest,
    collection: _CandidateCollection,
    fact_set_id: str,
) -> None:
    connection.execute(
        """
        INSERT INTO legacy_attribution_fact_sets(
            id, start_at, end_at, approved_by, denominator, accepted, rejected,
            unresolved, source_ids_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fact_set_id,
            request.start.astimezone(UTC).isoformat(),
            request.end.astimezone(UTC).isoformat(),
            request.approved_by.strip(),
            collection.denominator,
            len(collection.candidates),
            collection.rejected,
            collection.unresolved,
            json.dumps(sorted(collection.source_ids), separators=(",", ":")),
            datetime.now(UTC).isoformat(),
        ),
    )


def _persist_candidate(
    connection: sqlite3.Connection, candidate: _Candidate, fact_set_id: str
) -> tuple[str, str, RemoteEventReference]:
    project = canonical_git_project(candidate.workspace)
    _project_identity(connection, project)
    activity = CanonicalActivity(
        producer="codex-cli",
        producer_surface="codex-cli",
        correlation_id=candidate.correlation_id,
        source_started_at_ns=candidate.timestamp_ns,
        source_ended_at_ns=candidate.timestamp_ns,
        detector_id=_DETECTOR_ID,
        detector_version=1,
        normalization_version=1,
        source_membership=CanonicalSourceMembership(log_ids=(candidate.log_id,)),
        operation_kind="exec",
        target_kind="workspace_target",
        normalized_target=candidate.target,
        normalized_failure_class="",
        created_at=datetime.now(UTC).isoformat(),
    )
    attribution = CanonicalAttribution(
        state="resolved",
        project_identity_id=project.identity,
        method="legacy_structured_exec",
        evidence_id=hashlib.sha256(
            f"{candidate.log_id}:{candidate.call_id}:{candidate.tool_name}".encode()
        ).hexdigest(),
        reason_code=None,
        created_at=datetime.now(UTC).isoformat(),
    )
    write = persist_canonical_activity(connection, activity, attribution)
    if not write.version_inserted:
        raise RuntimeError(f"legacy fact set {fact_set_id} was already applied")
    event_id = enqueue_canonical_activity_version(
        connection,
        CanonicalActivityVersionEvent(
            activity_id=write.activity_id,
            version=write.version,
            timestamp_ns=candidate.timestamp_ns,
            attributes=canonical_activity_event_attributes(connection, activity, attribution),
        ),
    )
    return (
        write.activity_id,
        event_id,
        RemoteEventReference(
            event_id=event_id,
            event_name=CANONICAL_ACTIVITY_EVENT_NAME,
            timestamp_ns=candidate.timestamp_ns,
        ),
    )


def _persist_candidates(
    connection: sqlite3.Connection,
    collection: _CandidateCollection,
    fact_set_id: str,
) -> _PersistedActivities:
    activity_ids: list[str] = []
    outbox_ids: list[str] = []
    remote_references: list[RemoteEventReference] = []
    for candidate in sorted(
        collection.candidates, key=lambda value: (value.timestamp_ns, value.log_id)
    ):
        activity_id, event_id, remote_reference = _persist_candidate(
            connection, candidate, fact_set_id
        )
        activity_ids.append(activity_id)
        outbox_ids.append(event_id)
        remote_references.append(remote_reference)
    return _PersistedActivities(
        activity_ids=tuple(activity_ids),
        outbox_ids=tuple(outbox_ids),
        remote_references=tuple(remote_references),
    )


def _verify_delivery(
    connection: sqlite3.Connection, request: _DeliveryVerificationRequest
) -> dict[str, Any]:
    persisted = request.persisted
    delivery = (
        drain_outbox_event_ids(
            connection,
            persisted.outbox_ids,
            endpoint=request.attribution_request.delivery_endpoint,
        )
        if persisted.outbox_ids
        else {"selected": 0, "delivered": 0, "pending": 0}
    )
    remote_ids = (
        remote_event_ids(request.client, persisted.remote_references)
        if persisted.remote_references
        else set()
    )
    verification = _append_delivery_attempt(
        connection,
        fact_set_id=request.fact_set_id,
        intended_ids=persisted.outbox_ids,
        delivery=delivery,
        remote_ids=remote_ids,
    )
    if not verification["verified"]:
        raise RuntimeError(
            f"legacy fact set {request.fact_set_id} delivery verification failed: "
            f"{verification['failure_reason']}"
        )
    return verification


def run_legacy_project_attribution(
    connection: sqlite3.Connection,
    client: ClickHouseClient,
    request: LegacyProjectAttributionRequest,
) -> dict[str, Any]:
    """Apply one explicit bounded legacy fact set, refusing repeat application."""
    _validate_attribution_request(request)
    collection = _collect_candidates(client, request)
    fact_set_id = _fact_set_identity(
        start=request.start, end=request.end, source_ids=collection.source_ids
    )
    if (
        connection.execute(
            "SELECT 1 FROM legacy_attribution_fact_sets WHERE id = ?", (fact_set_id,)
        ).fetchone()
        is not None
    ):
        raise RuntimeError(f"legacy fact set {fact_set_id} was already applied")
    with connection:
        _record_fact_set(connection, request, collection, fact_set_id)
        persisted = _persist_candidates(connection, collection, fact_set_id)
    verification = _verify_delivery(
        connection,
        _DeliveryVerificationRequest(
            client=client,
            attribution_request=request,
            fact_set_id=fact_set_id,
            persisted=persisted,
        ),
    )
    return {
        "status": "applied",
        "approved_by": request.approved_by,
        "fact_set_id": fact_set_id,
        "accepted": len(persisted.activity_ids),
        "rejected": collection.rejected,
        "unresolved": collection.unresolved,
        "denominator": collection.denominator,
        "activity_ids": list(persisted.activity_ids),
        "outbox_event_ids": list(persisted.outbox_ids),
        **verification,
    }

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
    CanonicalActivityVersionEvent,
    enqueue_canonical_activity_version,
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
        or tool_name is None
        or tool_name not in _ALLOWED_TOOL_NAMES
    ):
        return None
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
        return None
    raw_arguments = row.get("arguments")
    if not isinstance(raw_arguments, str):
        return None
    try:
        arguments = parse_tool_arguments(raw_arguments)
    except NormalizationError:
        return None
    if not isinstance(arguments, Mapping) or set(arguments) - _ALLOWED_ARGUMENT_KEYS:
        return None
    command = arguments.get("cmd")
    workdir = arguments.get("workdir")
    if not isinstance(command, str) or not command:
        return None
    if not isinstance(workdir, str) or not workdir:
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


def run_legacy_project_attribution(
    connection: sqlite3.Connection,
    config: AppConfig,
    *,
    client: ClickHouseClient,
    start: datetime,
    end: datetime,
    approved_by: str,
) -> dict[str, Any]:
    """Apply one explicit bounded legacy fact set, refusing repeat application."""
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError("start and end must be ordered, timezone-aware datetimes")
    if not approved_by.strip():
        raise ValueError("approved_by must be non-empty")
    maximum = timedelta(hours=config.legacy_project_attribution.maximum_range_hours)
    if end - start > maximum:
        raise ValueError(MAXIMUM_RANGE_HELP)
    roots = config.legacy_project_attribution.project_roots
    if not roots:
        raise ValueError("legacy_project_attribution.project_roots must be explicitly configured")

    candidates: list[_Candidate] = []
    source_ids: list[str] = []
    denominator = 0
    rejected = 0
    unresolved = 0
    for row in client.query(
        LEGACY_PROJECT_ATTRIBUTION_QUERY,
        {"start_ns": _timestamp_ns(start), "end_ns": _timestamp_ns(end)},
    ):
        denominator += 1
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

    fact_set_id = _fact_set_identity(start=start, end=end, source_ids=source_ids)
    if (
        connection.execute(
            "SELECT 1 FROM legacy_attribution_fact_sets WHERE id = ?", (fact_set_id,)
        ).fetchone()
        is not None
    ):
        raise RuntimeError(f"legacy fact set {fact_set_id} was already applied")

    accepted_ids: list[str] = []
    outbox_ids: list[str] = []
    with connection:
        connection.execute(
            """
            INSERT INTO legacy_attribution_fact_sets(
                id, start_at, end_at, approved_by, denominator, accepted, rejected,
                unresolved, source_ids_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fact_set_id,
                start.astimezone(UTC).isoformat(),
                end.astimezone(UTC).isoformat(),
                approved_by.strip(),
                denominator,
                len(candidates),
                rejected,
                unresolved,
                json.dumps(sorted(source_ids), separators=(",", ":")),
                datetime.now(UTC).isoformat(),
            ),
        )
        for candidate in sorted(candidates, key=lambda value: (value.timestamp_ns, value.log_id)):
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
            evidence_id = hashlib.sha256(
                f"{candidate.log_id}:{candidate.call_id}:{candidate.tool_name}".encode()
            ).hexdigest()
            attribution = CanonicalAttribution(
                state="resolved",
                project_identity_id=project.identity,
                method="legacy_structured_exec",
                evidence_id=evidence_id,
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
                    attributes=canonical_activity_event_attributes(
                        connection, activity, attribution
                    ),
                ),
            )
            accepted_ids.append(write.activity_id)
            outbox_ids.append(event_id)
    return {
        "status": "applied",
        "approved_by": approved_by,
        "fact_set_id": fact_set_id,
        "accepted": len(accepted_ids),
        "rejected": rejected,
        "unresolved": unresolved,
        "denominator": denominator,
        "activity_ids": accepted_ids,
        "outbox_event_ids": outbox_ids,
    }

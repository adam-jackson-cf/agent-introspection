"""Allowlisted, deterministic provenance mapping for legacy Codex artifacts."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal

from agent_introspection.project_evidence import (
    GitWorkspaceResolver,
    WorkspaceProjectResolver,
)

_SCHEMA_VERSION = 1
_ORIGINATORS = {
    "codex-tui": ("codex-cli", "codex-cli"),
    "Codex Desktop": ("codex-app-server", "codex-app"),
}


@dataclass(frozen=True, slots=True)
class LegacyMappingRecord:
    """One provenance-only disposition; it never carries artifact content."""

    observation_id: str
    producer: str | None
    producer_surface: str | None
    correlation_id: str | None
    source_at_ns: int
    source_ids: tuple[str, ...]
    project: tuple[str, str, str, str] | None
    status: Literal["accepted", "rejected", "unresolved"]
    reason_code: str | None
    evidence_ids: tuple[str, ...]
    evidence_hash: str


@dataclass(frozen=True, slots=True)
class LegacyMappingManifest:
    """Immutable, canonical rendering of a complete mapping population."""

    schema_version: int
    created_at: str
    population_hash: str
    rows: tuple[LegacyMappingRecord, ...]
    accepted: int
    rejected: int
    unresolved: int
    denominator: int
    checksum: str


@dataclass(frozen=True, slots=True)
class LegacyObservation:
    """Frozen input used by the mapper; all identifiers are producer-independent."""

    observation_id: str
    correlation_id: str | None
    source_at_ns: int
    source_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    identity_kind: Literal["session", "thread", "conversation", "episode"] = "session"


@dataclass(frozen=True, slots=True)
class _SessionMeta:
    session_id: str
    timestamp_ns: int
    cwd: str | None
    producer: str
    surface: str


@dataclass(frozen=True, slots=True)
class _Workspace:
    timestamp_ns: int
    cwd: str


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()


def _digest(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value and value.strip() == value else None


def _timestamp_ns(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return int(parsed.astimezone(UTC).timestamp() * 1_000_000_000)


def _jsonl_files(roots: Iterable[Path]) -> Iterable[Path]:
    for configured_root in roots:
        root = Path(configured_root).expanduser().resolve(strict=False)
        if root.is_file() and root.suffix == ".jsonl":
            yield root
        elif root.is_dir():
            yield from sorted(path for path in root.rglob("*.jsonl") if path.is_file())


def _load_indexes(
    roots: Iterable[Path],
) -> tuple[
    dict[str, tuple[_SessionMeta, ...]],
    dict[str, tuple[str, str, int]],
    dict[str, tuple[_Workspace, ...]],
    set[str],
    set[str],
]:
    sessions: dict[str, list[_SessionMeta]] = {}
    traces: dict[str, tuple[str, str, int]] = {}
    workspaces: dict[str, list[_Workspace]] = {}
    session_conflicts: set[str] = set()
    trace_conflicts: set[str] = set()
    for path in _jsonl_files(roots):
        current_session: str | None = None
        try:
            stream = path.open("r", encoding="utf-8")
        except OSError:
            continue
        with stream:
            for line in stream:
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(raw, dict):
                    continue
                record_type = raw.get("type")
                payload = raw.get("payload", raw)
                if not isinstance(payload, dict):
                    continue
                if record_type == "session_meta":
                    session_id = _text(payload.get("id")) or _text(payload.get("session_id"))
                    timestamp = _timestamp_ns(payload.get("timestamp"))
                    originator = _text(payload.get("originator"))
                    if session_id is None or timestamp is None or originator not in _ORIGINATORS:
                        continue
                    cwd = _text(payload.get("cwd"))
                    producer, surface = _ORIGINATORS[originator]
                    meta = _SessionMeta(session_id, timestamp, cwd, producer, surface)
                    existing = sessions.setdefault(session_id, [])
                    if existing and meta not in existing:
                        session_conflicts.add(session_id)
                    elif meta not in existing:
                        existing.append(meta)
                    current_session = session_id
                elif record_type == "turn_context":
                    timestamp = _timestamp_ns(payload.get("timestamp")) or _timestamp_ns(
                        raw.get("timestamp")
                    )
                    cwd = _text(payload.get("cwd"))
                    session_id = _text(payload.get("session_id")) or current_session
                    if session_id and timestamp is not None and cwd is not None:
                        workspaces.setdefault(session_id, []).append(_Workspace(timestamp, cwd))
                elif record_type == "event_msg" and payload.get("type") == "task_started":
                    trace_id = _text(payload.get("trace_id"))
                    turn_id = _text(payload.get("turn_id"))
                    started = _timestamp_ns(payload.get("started_at"))
                    if started is None:
                        started = _timestamp_ns(raw.get("timestamp"))
                    if trace_id and turn_id and started is not None and current_session:
                        task = (current_session, turn_id, started)
                        prior = traces.get(trace_id)
                        if prior is not None and prior != task:
                            trace_conflicts.add(trace_id)
                        else:
                            traces[trace_id] = task
    return (
        {
            key: tuple(sorted(value, key=lambda item: item.timestamp_ns))
            for key, value in sessions.items()
        },
        dict(traces),
        {
            key: tuple(sorted(value, key=lambda item: item.timestamp_ns))
            for key, value in workspaces.items()
        },
        session_conflicts,
        trace_conflicts,
    )


def _record(
    observation: LegacyObservation,
    *,
    correlation_id: str | None,
    producer: str | None,
    surface: str | None,
    project: tuple[str, str, str, str] | None,
    status: Literal["accepted", "rejected", "unresolved"],
    reason: str | None,
) -> LegacyMappingRecord:
    source_ids = tuple(sorted(set(observation.source_ids)))
    evidence_ids = tuple(sorted(set(observation.evidence_ids)))
    return LegacyMappingRecord(
        observation.observation_id,
        producer,
        surface,
        correlation_id,
        observation.source_at_ns,
        source_ids,
        project,
        status,
        reason,
        evidence_ids,
        _digest({"source_ids": source_ids, "evidence_ids": evidence_ids}),
    )


def build_legacy_mapping_manifest(
    observations: Sequence[LegacyObservation],
    *,
    codex_jsonl_roots: Iterable[Path],
    project_roots: Iterable[Path],
    created_at: str,
    resolver: WorkspaceProjectResolver | None = None,
) -> LegacyMappingManifest:
    """Map frozen observations without retaining unallowlisted JSONL data."""

    if not isinstance(created_at, str) or not created_at:
        raise ValueError("created_at is required")
    observed_ids = [row.observation_id for row in observations]
    if any(not isinstance(item, str) or not item for item in observed_ids) or len(
        set(observed_ids)
    ) != len(observed_ids):
        raise ValueError("observations require unique non-empty identifiers")
    sessions, traces, workspaces, session_conflicts, trace_conflicts = _load_indexes(
        codex_jsonl_roots
    )
    workspace_resolver = resolver or GitWorkspaceResolver(project_roots=project_roots)
    records: list[LegacyMappingRecord] = []
    for observation in sorted(observations, key=lambda item: item.observation_id):
        correlation_id = _text(observation.correlation_id)
        if correlation_id is None:
            records.append(
                _record(
                    observation,
                    correlation_id=None,
                    producer=None,
                    surface=None,
                    project=None,
                    status="unresolved",
                    reason="missing_correlation_id",
                )
            )
            continue
        session_id = correlation_id
        if observation.identity_kind == "episode":
            if session_id in trace_conflicts:
                records.append(
                    _record(
                        observation,
                        correlation_id=correlation_id,
                        producer=None,
                        surface=None,
                        project=None,
                        status="rejected",
                        reason="duplicate_conflict",
                    )
                )
                continue
            trace = traces.get(session_id)
            if trace is None:
                records.append(
                    _record(
                        observation,
                        correlation_id=correlation_id,
                        producer=None,
                        surface=None,
                        project=None,
                        status="unresolved",
                        reason="missing_correlation_id",
                    )
                )
                continue
            session_id = trace[0]
        if session_id in session_conflicts:
            records.append(
                _record(
                    observation,
                    correlation_id=correlation_id,
                    producer=None,
                    surface=None,
                    project=None,
                    status="rejected",
                    reason="duplicate_conflict",
                )
            )
            continue
        session_candidates = [
            item
            for item in sessions.get(session_id, ())
            if item.timestamp_ns <= observation.source_at_ns
        ]
        if not session_candidates:
            records.append(
                _record(
                    observation,
                    correlation_id=correlation_id,
                    producer=None,
                    surface=None,
                    project=None,
                    status="unresolved",
                    reason="missing_session_context",
                )
            )
            continue
        session = session_candidates[-1]
        candidates = [
            item
            for item in workspaces.get(session_id, ())
            if item.timestamp_ns <= observation.source_at_ns
        ]
        cwd = candidates[-1].cwd if candidates else session.cwd
        if cwd is None:
            records.append(
                _record(
                    observation,
                    correlation_id=correlation_id,
                    producer=session.producer,
                    surface=session.surface,
                    project=None,
                    status="unresolved",
                    reason="missing_workspace",
                )
            )
            continue
        resolution = workspace_resolver.resolve(cwd)
        if resolution.status != "project" or resolution.project is None:
            reason = (
                "outside_collection"
                if resolution.status == "outside_collection"
                else "invalid_workspace"
            )
            records.append(
                _record(
                    observation,
                    correlation_id=correlation_id,
                    producer=session.producer,
                    surface=session.surface,
                    project=None,
                    status="unresolved",
                    reason=reason,
                )
            )
            continue
        project = resolution.project
        records.append(
            _record(
                observation,
                correlation_id=session_id,
                producer=session.producer,
                surface=session.surface,
                project=(
                    project.identity,
                    project.display_name or project.root.name,
                    project.root.as_posix(),
                    project.kind,
                ),
                status="accepted",
                reason=None,
            )
        )
    rows = tuple(records)
    accepted = sum(row.status == "accepted" for row in rows)
    rejected = sum(row.status == "rejected" for row in rows)
    unresolved = sum(row.status == "unresolved" for row in rows)
    population = [_row_data(row) for row in rows]
    population_hash = _digest(population)
    body = {
        "schema_version": _SCHEMA_VERSION,
        "created_at": created_at,
        "population_hash": population_hash,
        "rows": population,
        "accepted": accepted,
        "rejected": rejected,
        "unresolved": unresolved,
        "denominator": len(rows),
    }
    return LegacyMappingManifest(
        _SCHEMA_VERSION,
        created_at,
        population_hash,
        rows,
        accepted,
        rejected,
        unresolved,
        len(rows),
        _digest(body),
    )


def _row_data(row: LegacyMappingRecord) -> dict[str, object]:
    return asdict(row)

"""Evidence-bounded producer artifact backfill for session context."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_introspection.identities import IdentityError, ProjectIdentity, normalize_target
from agent_introspection.session_context import Producer, SessionContextEvent, spool_event

SCAFFOLD_BASENAMES = frozenset(
    {
        "README.md",
        "LICENSE",
        ".gitignore",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "Cargo.lock",
        "uv.lock",
        "poetry.lock",
    }
)
DEFAULT_ROOTS: dict[Producer, tuple[Path, ...]] = {
    "claude-code": (Path.home() / ".claude" / "projects",),
    "codex-cli": (Path.home() / ".codex" / "sessions",),
    "omp": (Path.home() / ".omp" / "agent" / "sessions",),
}
_CONTENT_KEYS = frozenset({"content", "text", "prompt", "instructions", "reasoning"})


@dataclass(slots=True)
class _Evidence:
    session_ids: set[str] = field(default_factory=set)
    workspaces: set[str] = field(default_factory=set)
    timestamps: list[datetime] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    invalid_metadata: bool = False


def _safe_content(value: Any) -> Any:
    if isinstance(value, list):
        return [_safe_content(item) for item in value if isinstance(item, (dict, list))]
    if isinstance(value, dict):
        return {
            key: _safe_content(child)
            for key, child in value.items()
            if key
            in {
                "type",
                "name",
                "tool_name",
                "input",
                "arguments",
                "file_path",
                "path",
                "target_path",
            }
        }
    return value


def _safe_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    return {
        key: _safe_content(value) if key == "content" else value
        for key, value in pairs
        if key not in _CONTENT_KEYS or key == "content"
    }


def _records(path: Path) -> Iterable[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8") as handle:
            if path.suffix == ".json":
                value = json.load(handle, object_pairs_hook=_safe_object)
                values = value if isinstance(value, list) else (value,)
                yield from (item for item in values if isinstance(item, dict))
                return
            for line in handle:
                if line.strip():
                    value = json.loads(line, object_pairs_hook=_safe_object)
                    if isinstance(value, dict):
                        yield value
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() == value and value else None


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


def _find_values(value: object, keys: frozenset[str]) -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in keys:
                found = _text(child)
                if found is not None:
                    yield found
            yield from _find_values(child, keys)
    elif isinstance(value, list):
        for child in value:
            yield from _find_values(child, keys)


def _tool_targets(record: dict[str, Any]) -> Iterable[str]:
    names = {name.lower() for name in _find_values(record, frozenset({"name", "tool_name"}))}
    record_type = _text(record.get("type")) or ""
    editable = any(
        token in name for name in names for token in ("write", "edit", "patch")
    ) or record_type in {"function_call", "custom_tool_call"}
    if editable:
        yield from _find_values(record, frozenset({"file_path", "path", "target_path"}))


def _record_evidence(producer: str, record: dict[str, Any], evidence: _Evidence) -> None:
    if producer == "claude-code":
        session_keys = {"sessionId", "originSessionId"}
    elif producer == "codex-cli":
        session_keys = {"session_id", "sessionId"}
        if record.get("type") == "session_meta":
            payload = record.get("payload")
            if isinstance(payload, dict):
                session_id = _text(payload.get("id"))
                if session_id is not None:
                    evidence.session_ids.add(session_id)
    else:
        session_keys = {"session_id", "sessionId"}
    if record.get("type") == "session":
        session_id = _text(record.get("id"))
        if session_id is not None:
            evidence.session_ids.add(session_id)
    evidence.session_ids.update(_find_values(record, frozenset(session_keys)))
    evidence.workspaces.update(_find_values(record, frozenset({"cwd", "project", "workspace"})))
    timestamp_values = list(
        _find_values(record, frozenset({"timestamp", "startedAt", "updatedAt"}))
    )
    for value in timestamp_values:
        timestamp = _timestamp(value)
        if timestamp is None:
            evidence.invalid_metadata = True
        else:
            evidence.timestamps.append(timestamp)
    evidence.targets.extend(_tool_targets(record))


def _git_root(workspace: str) -> Path | None:
    try:
        current = Path(workspace).expanduser().resolve(strict=True)
    except OSError:
        return None
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _event_id(producer: str, session_id: str, occurred_at: datetime, root: Path) -> str:
    canonical = "\0".join(
        (producer, session_id, "session_start", occurred_at.isoformat(), root.as_posix())
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _project_id(root: Path) -> str:
    return hashlib.sha256(b"git\0" + root.as_posix().encode()).hexdigest()


def _iter_jsonl(roots: Iterable[Path]) -> Iterable[Path]:
    for root in roots:
        if root.exists():
            yield from sorted(path for path in root.rglob("*.jsonl") if path.is_file())
            yield from sorted(path for path in root.rglob("*.json") if path.is_file())


def backfill(*, roots: Mapping[Producer, Iterable[Path]], inbox: Path) -> dict[str, object]:
    """Spool only producer-session consensus records and return count-only results."""
    evidence: dict[tuple[Producer, str], _Evidence] = defaultdict(_Evidence)
    scanned = 0
    rejection = Counter[str]()
    for producer, producer_roots in roots.items():
        for artifact in _iter_jsonl(producer_roots):
            scanned += 1
            local = _Evidence()
            for record in _records(artifact):
                _record_evidence(producer, record, local)
            if len(local.session_ids) != 1:
                rejection[
                    "conflicting_session_ids" if local.session_ids else "missing_session_id"
                ] += 1
                continue
            session_id = next(iter(local.session_ids))
            aggregate = evidence[(producer, session_id)]
            aggregate.session_ids.update(local.session_ids)
            aggregate.workspaces.update(local.workspaces)
            aggregate.timestamps.extend(local.timestamps)
            aggregate.targets.extend(local.targets)
            aggregate.invalid_metadata |= local.invalid_metadata
    spooled = 0
    eligible = 0
    unresolved = 0
    for (producer, session_id), item in evidence.items():
        if item.invalid_metadata or not item.timestamps or not item.workspaces:
            rejection["malformed_or_missing_metadata"] += 1
            unresolved += 1
            continue
        roots_by_workspace = {_git_root(workspace) for workspace in item.workspaces}
        if None in roots_by_workspace:
            rejection["non_git_workspace"] += 1
            unresolved += 1
            continue
        project_roots = {root for root in roots_by_workspace if root is not None}
        if len(project_roots) != 1:
            rejection["multiple_project_roots"] += 1
            unresolved += 1
            continue
        root = next(iter(project_roots))
        normalized: list[str] = []
        target_rejected = False
        for target in item.targets:
            try:
                normalized.append(normalize_target(target, project_root=root))
            except (IdentityError, OSError):
                target_rejected = True
        if target_rejected:
            rejection["target_outside_project"] += 1
            unresolved += 1
            continue
        counts = Counter(
            target for target in normalized if Path(target).name not in SCAFFOLD_BASENAMES
        )
        if not counts:
            rejection["no_non_scaffold_write_target"] += 1
            unresolved += 1
            continue
        target, count = counts.most_common(1)[0]
        if count < 2:
            rejection["insufficient_target_consensus"] += 1
            unresolved += 1
            continue
        occurred_at = min(item.timestamps)
        event = SessionContextEvent(
            _event_id(producer, session_id, occurred_at, root),
            producer,
            session_id,
            "session_start",
            occurred_at,
            ProjectIdentity("git", root, _project_id(root), root.name),
        )
        eligible += 1
        before = (inbox / f"{event.event_id}.json").exists()
        spool_event(event, directory=inbox)
        spooled += int(not before)
    return {
        "scanned": scanned,
        "eligible": eligible,
        "spooled": spooled,
        "unresolved": unresolved
        + sum(rejection[key] for key in ("conflicting_session_ids", "missing_session_id")),
        "rejections": dict(sorted(rejection.items())),
    }

"""Allowlisted evidence hydration with mandatory secret redaction."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class EvidenceError(RuntimeError):
    """Evidence cannot be safely correlated or retained."""


SECRET_PATTERNS = (
    re.compile(r"(?i)\b(api[_-]?key|token|password|secret)\b\s*[:=]\s*['\"]?([^\s'\"]+)"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[oprsu]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
)


@dataclass(frozen=True, slots=True)
class HydratedEvidence:
    correlation_status: str
    redacted_content: str | None
    content_hash: str
    source_reference: str


def redact_text(value: str) -> str:
    """Replace recognized secret values and fail on unsafe input types or size."""
    if len(value) > 1_000_000:
        raise EvidenceError("evidence exceeds the secret scanner size limit")
    redacted = value
    for pattern in SECRET_PATTERNS:
        try:
            if pattern.groups == 2:
                redacted = pattern.sub(lambda match: f"{match.group(1)}=[REDACTED]", redacted)
            else:
                redacted = pattern.sub("[REDACTED]", redacted)
        except re.error as exc:
            raise EvidenceError("secret scanner failed") from exc
    return redacted


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hydrate_allowlisted_fields(
    *,
    source_reference: str,
    fields: dict[str, Any],
    allowed_fields: frozenset[str],
) -> HydratedEvidence:
    """Retain only explicitly allowed fields after secret scanning."""
    selected = {key: fields[key] for key in sorted(fields) if key in allowed_fields}
    try:
        raw = json.dumps(selected, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        redacted = redact_text(raw)
    except (TypeError, ValueError, EvidenceError):
        return HydratedEvidence(
            correlation_status="quarantined",
            redacted_content=None,
            content_hash=_hash("quarantined"),
            source_reference=source_reference,
        )
    return HydratedEvidence(
        correlation_status="correlated",
        redacted_content=redacted,
        content_hash=_hash(redacted),
        source_reference=source_reference,
    )


def enrich_from_session_jsonl(
    path: Path,
    *,
    task_id: str,
    trace_id: str | None,
    turn_id: str | None,
) -> HydratedEvidence:
    """Read session JSONL only as correlated enrichment for an existing candidate."""
    if not path.is_file():
        raise EvidenceError("session JSONL does not exist")
    matched: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvidenceError(f"invalid session JSONL at line {line_number}") from exc
        if not isinstance(item, dict):
            continue
        item_task = item.get("thread_id") or item.get("conversation_id")
        correlated = item_task == task_id and (
            trace_id is None
            or item.get("trace_id") == trace_id
            or (turn_id is not None and item.get("turn_id") == turn_id)
        )
        if correlated:
            matched.append(
                {
                    key: item[key]
                    for key in ("assistant_output", "tool_name", "call_id", "turn_id", "sequence")
                    if key in item
                }
            )
    if not matched:
        return HydratedEvidence(
            correlation_status="pending",
            redacted_content=None,
            content_hash=_hash("pending"),
            source_reference=str(path),
        )
    return hydrate_allowlisted_fields(
        source_reference=str(path),
        fields={"events": matched},
        allowed_fields=frozenset({"events"}),
    )

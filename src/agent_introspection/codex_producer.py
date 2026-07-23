"""Build invocation-scoped Codex telemetry commands from explicit metadata."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

__all__ = [
    "CodexProjectMetadata",
    "build_codex_command",
    "load_project_metadata",
]

_METADATA_KEYS = (
    "agent.project.id",
    "agent.project.name",
    "agent.project.root",
    "agent.project.kind",
)


@dataclass(frozen=True)
class CodexProjectMetadata:
    """Explicit project attributes to attach to one Codex invocation."""

    id: str
    name: str
    root: str
    kind: str


def load_project_metadata(path: Path) -> CodexProjectMetadata:
    """Load and validate a canonical project metadata JSON document."""
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("project metadata JSON is unreadable or invalid") from exc

    if not isinstance(value, dict):
        raise ValueError("project metadata JSON must contain an object")
    if set(value) != set(_METADATA_KEYS):
        raise ValueError("project metadata JSON must contain exactly the canonical fields")

    project_id = _require_nonempty_string(value["agent.project.id"], "agent.project.id")
    name = _require_nonempty_string(value["agent.project.name"], "agent.project.name")
    root = _require_normalized_absolute_posix_path(value["agent.project.root"])
    kind = _require_nonempty_string(value["agent.project.kind"], "agent.project.kind")
    if kind not in {"git", "non_git"}:
        raise ValueError("agent.project.kind must be git or non_git")

    return CodexProjectMetadata(id=project_id, name=name, root=root, kind=kind)


def build_codex_command(
    *,
    executable: str,
    metadata: CodexProjectMetadata,
    arguments: Sequence[str],
) -> tuple[str, ...]:
    """Build a shell-free argv tuple for one attributed Codex invocation."""
    attributes = (
        ("agent.project.id", metadata.id),
        ("agent.project.name", metadata.name),
        ("agent.project.root", metadata.root),
        ("agent.project.kind", metadata.kind),
    )
    assignment = (
        "otel.span_attributes = { "
        + ", ".join(f"{json.dumps(key)} = {json.dumps(value)}" for key, value in attributes)
        + " }"
    )
    return (executable, "-c", assignment, "-C", metadata.root, *arguments)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _require_nonempty_string(value: object, key: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _require_normalized_absolute_posix_path(value: object) -> str:
    root = _require_nonempty_string(value, "agent.project.root")
    path = PurePosixPath(root)
    if (
        not path.is_absolute()
        or path.as_posix() != root
        or any(part in {".", ".."} for part in path.parts)
    ):
        raise ValueError("agent.project.root must be a normalized absolute POSIX path")
    return root

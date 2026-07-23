"""Discover Git project attribution and build Codex telemetry commands."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "CodexProjectMetadata",
    "build_codex_command",
    "discover_git_project",
]


@dataclass(frozen=True)
class CodexProjectMetadata:
    """Canonical Git project attributes for one Codex invocation."""

    id: str
    name: str
    root: str
    kind: str


def discover_git_project(workspace: Path) -> CodexProjectMetadata | None:
    """Resolve canonical Git project attribution for a requested workspace."""
    try:
        result = subprocess.run(
            ("git", "-C", str(workspace), "rev-parse", "--git-common-dir"),
            capture_output=True,
            check=False,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("unable to resolve Git project context") from exc

    if result.returncode:
        if _is_non_git_repository(result):
            return None
        raise ValueError("unable to resolve Git project context")

    common_dir = _resolve_common_dir(workspace, result.stdout)
    project_root = common_dir.parent
    project_root_path = project_root.as_posix()
    return CodexProjectMetadata(
        id=f"git:{hashlib.sha256(b'git\x00' + project_root_path.encode('utf-8')).hexdigest()}",
        name=project_root.name,
        root=project_root.as_posix(),
        kind="git",
    )


def build_codex_command(
    *,
    executable: str,
    workspace: Path,
    metadata: CodexProjectMetadata | None,
    arguments: Sequence[str],
) -> tuple[str, ...]:
    """Build a shell-free argv tuple for a Codex invocation."""
    if metadata is None:
        return (executable, "-C", str(workspace), *arguments)

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


def _is_non_git_repository(result: subprocess.CompletedProcess[str]) -> bool:
    return result.returncode == 128 and "not a git repository" in result.stderr.lower()


def _resolve_common_dir(workspace: Path, output: str) -> Path:
    common_dir_value = output.removesuffix("\n").removesuffix("\r")
    if (
        not common_dir_value
        or "\x00" in common_dir_value
        or "\n" in common_dir_value
        or "\r" in common_dir_value
    ):
        raise ValueError("Git returned an invalid common directory")

    workspace_path = workspace.resolve(strict=False)
    common_dir = Path(common_dir_value)
    if not common_dir.is_absolute():
        common_dir = workspace_path / common_dir

    try:
        common_dir = common_dir.resolve(strict=True)
    except OSError as exc:
        raise ValueError("Git common directory does not exist") from exc

    if common_dir.name != ".git":
        raise ValueError("Git common directory is not a .git directory")
    return common_dir

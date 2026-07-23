from __future__ import annotations

import hashlib
import subprocess
import tomllib
from pathlib import Path

import pytest

import agent_introspection.codex_producer as codex_producer
from agent_introspection.codex_producer import (
    CodexProjectMetadata,
    build_codex_command,
    discover_git_project,
)


def mock_git_common_dir(
    monkeypatch: pytest.MonkeyPatch, result: subprocess.CompletedProcess[str]
) -> list[tuple[object, ...]]:
    calls: list[tuple[object, ...]] = []

    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return result

    monkeypatch.setattr(codex_producer.subprocess, "run", run)
    return calls


def test_discover_git_project_derives_canonical_metadata_from_common_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "repository" / "src"
    workspace.mkdir(parents=True)
    common_dir = tmp_path / "repository" / ".git"
    common_dir.mkdir()
    calls = mock_git_common_dir(
        monkeypatch,
        subprocess.CompletedProcess(("git",), 0, f"{common_dir}\n", ""),
    )

    metadata = discover_git_project(workspace)

    expected_root = common_dir.parent.resolve()
    assert metadata == CodexProjectMetadata(
        id=f"git:{hashlib.sha256(f'git\0{expected_root.as_posix()}'.encode()).hexdigest()}",
        name=expected_root.name,
        root=expected_root.as_posix(),
        kind="git",
    )
    assert calls == [(("git", "-C", str(workspace), "rev-parse", "--git-common-dir"),)]


def test_discover_git_project_resolves_relative_common_directory_against_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "repository" / "nested" / "workspace"
    workspace.mkdir(parents=True)
    common_dir = tmp_path / "repository" / ".git"
    common_dir.mkdir()
    mock_git_common_dir(
        monkeypatch,
        subprocess.CompletedProcess(("git",), 0, "../../.git\n", ""),
    )

    metadata = discover_git_project(workspace)

    assert metadata == CodexProjectMetadata(
        id=f"git:{hashlib.sha256(f'git\0{common_dir.parent.resolve().as_posix()}'.encode()).hexdigest()}",
        name="repository",
        root=common_dir.parent.resolve().as_posix(),
        kind="git",
    )


def test_discover_git_project_uses_root_identity_for_worktrees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    common_dir = repository / ".git"
    common_dir.mkdir(parents=True)
    worktree_a = tmp_path / "worktree-a"
    worktree_b = tmp_path / "worktree-b"
    worktree_a.mkdir()
    worktree_b.mkdir()
    outputs = iter((f"{common_dir}\n", f"{common_dir}\n"))

    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(("git",), 0, next(outputs), "")

    monkeypatch.setattr(codex_producer.subprocess, "run", run)

    first = discover_git_project(worktree_a)
    second = discover_git_project(worktree_b)

    assert first == second
    assert first == CodexProjectMetadata(
        id=f"git:{hashlib.sha256(f'git\0{repository.resolve().as_posix()}'.encode()).hexdigest()}",
        name="repository",
        root=repository.resolve().as_posix(),
        kind="git",
    )


def test_discover_git_project_returns_none_for_non_git_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "not-a-repository"
    workspace.mkdir()
    mock_git_common_dir(
        monkeypatch,
        subprocess.CompletedProcess(("git",), 128, "", "fatal: not a git repository"),
    )

    assert discover_git_project(workspace) is None


@pytest.mark.parametrize("output", ["", "not-git-dir\n", ".git/extra\n", ".git\nextra\n"])
def test_discover_git_project_fails_closed_for_malformed_common_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: str
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    mock_git_common_dir(monkeypatch, subprocess.CompletedProcess(("git",), 0, output, ""))

    with pytest.raises(ValueError):
        discover_git_project(workspace)


def test_discover_git_project_fails_closed_for_missing_common_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    mock_git_common_dir(
        monkeypatch,
        subprocess.CompletedProcess(("git",), 0, f"{workspace / '.git'}\n", ""),
    )

    with pytest.raises(ValueError):
        discover_git_project(workspace)


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError("git"),
        subprocess.CalledProcessError(1, ("git",)),
    ],
)
def test_discover_git_project_fails_closed_when_git_cannot_resolve_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise failure

    monkeypatch.setattr(codex_producer.subprocess, "run", run)

    with pytest.raises(ValueError):
        discover_git_project(workspace)


def test_build_codex_command_adds_attribution_and_preserves_exact_arguments() -> None:
    workspace = Path("/requested/workspace")
    metadata = CodexProjectMetadata(
        id="git:project-123",
        name="Introspection",
        root="/canonical/repository",
        kind="git",
    )
    arguments = ("exec", "--full-auto", "prompt with spaces", "--", "$(unexpanded)")

    command = build_codex_command(
        executable="/Applications/Codex CLI/codex",
        workspace=workspace,
        metadata=metadata,
        arguments=arguments,
    )

    assert command[0] == "/Applications/Codex CLI/codex"
    assert command[1] == "-c"
    assert command[3:5] == ("-C", "/canonical/repository")
    assert command[5:] == arguments
    assert tomllib.loads(command[2]) == {
        "otel": {
            "span_attributes": {
                "agent.project.id": "git:project-123",
                "agent.project.name": "Introspection",
                "agent.project.root": "/canonical/repository",
                "agent.project.kind": "git",
            }
        }
    }


def test_build_codex_command_omits_attributes_for_unresolved_workspace() -> None:
    workspace = Path("/requested/not-a-repository")
    arguments = ("exec", "--", "literal $input")

    command = build_codex_command(
        executable="codex",
        workspace=workspace,
        metadata=None,
        arguments=arguments,
    )

    assert command == ("codex", "-C", str(workspace), *arguments)


def test_build_codex_command_toml_escapes_attribute_values() -> None:
    metadata = CodexProjectMetadata(
        id='git:project-"quoted"\\path',
        name="Line\nbreak",
        root="/workspace/introspection",
        kind="git",
    )

    command = build_codex_command(
        executable="codex",
        workspace=Path("/requested/workspace"),
        metadata=metadata,
        arguments=(),
    )

    assert tomllib.loads(command[2]) == {
        "otel": {
            "span_attributes": {
                "agent.project.id": 'git:project-"quoted"\\path',
                "agent.project.name": "Line\nbreak",
                "agent.project.root": "/workspace/introspection",
                "agent.project.kind": "git",
            }
        }
    }

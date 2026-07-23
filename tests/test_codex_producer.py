import json
import tomllib
from pathlib import Path

import pytest

from agent_introspection.codex_producer import (
    CodexProjectMetadata,
    build_codex_command,
    load_project_metadata,
)

CANONICAL_METADATA = {
    "agent.project.id": "project-123",
    "agent.project.name": "Introspection",
    "agent.project.root": "/workspace/introspection",
    "agent.project.kind": "git",
}


def write_metadata(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "project-metadata.json"
    path.write_text(content)
    return path


def test_load_project_metadata_accepts_complete_canonical_tuple(tmp_path: Path) -> None:
    metadata_path = write_metadata(tmp_path, json.dumps(CANONICAL_METADATA))

    metadata = load_project_metadata(metadata_path)

    assert metadata == CodexProjectMetadata(
        id="project-123",
        name="Introspection",
        root="/workspace/introspection",
        kind="git",
    )


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"agent.project.id": "project-123"},
        {
            "agent.project.id": "project-123",
            "agent.project.name": "Introspection",
            "agent.project.root": "/workspace/introspection",
        },
    ],
)
def test_load_project_metadata_rejects_absent_or_partial_tuple(
    tmp_path: Path, document: dict[str, str]
) -> None:
    metadata_path = write_metadata(tmp_path, json.dumps(document))

    with pytest.raises(ValueError):
        load_project_metadata(metadata_path)


@pytest.mark.parametrize(
    "root",
    ["workspace/introspection", "/workspace/../introspection", ""],
)
def test_load_project_metadata_rejects_invalid_project_root(tmp_path: Path, root: str) -> None:
    document = {**CANONICAL_METADATA, "agent.project.root": root}
    metadata_path = write_metadata(tmp_path, json.dumps(document))

    with pytest.raises(ValueError):
        load_project_metadata(metadata_path)


@pytest.mark.parametrize("kind", ["", "workspace", "Git"])
def test_load_project_metadata_rejects_invalid_project_kind(tmp_path: Path, kind: str) -> None:
    document = {**CANONICAL_METADATA, "agent.project.kind": kind}
    metadata_path = write_metadata(tmp_path, json.dumps(document))

    with pytest.raises(ValueError):
        load_project_metadata(metadata_path)


@pytest.mark.parametrize("key", ["agent.project.id", "agent.project.name"])
def test_load_project_metadata_rejects_empty_required_string(tmp_path: Path, key: str) -> None:
    document = {**CANONICAL_METADATA, key: ""}
    metadata_path = write_metadata(tmp_path, json.dumps(document))

    with pytest.raises(ValueError):
        load_project_metadata(metadata_path)


def test_load_project_metadata_rejects_unknown_keys(tmp_path: Path) -> None:
    document = {**CANONICAL_METADATA, "agent.project.owner": "someone"}
    metadata_path = write_metadata(tmp_path, json.dumps(document))

    with pytest.raises(ValueError):
        load_project_metadata(metadata_path)


def test_load_project_metadata_rejects_malformed_json(tmp_path: Path) -> None:
    metadata_path = write_metadata(tmp_path, '{"agent.project.id":')

    with pytest.raises(ValueError):
        load_project_metadata(metadata_path)


def test_build_codex_command_uses_scoped_attributes_and_preserves_arguments() -> None:
    metadata = CodexProjectMetadata(
        id="project-123",
        name="Introspection",
        root="/workspace/introspection",
        kind="git",
    )
    arguments = ("exec", "--full-auto", "prompt with spaces", "--", "$(unexpanded)")

    command = build_codex_command(
        executable="/Applications/Codex CLI/codex",
        metadata=metadata,
        arguments=arguments,
    )

    assert command[0] == "/Applications/Codex CLI/codex"
    assert command[1] == "-c"
    assert command[3:5] == ("-C", "/workspace/introspection")
    assert command[5:] == arguments
    assert tomllib.loads(command[2]) == {"otel": {"span_attributes": CANONICAL_METADATA}}


def test_build_codex_command_toml_escapes_attribute_values() -> None:
    metadata = CodexProjectMetadata(
        id='project-"quoted"\\path',
        name="Line\nbreak",
        root="/workspace/introspection",
        kind="non_git",
    )

    command = build_codex_command(
        executable="codex",
        metadata=metadata,
        arguments=(),
    )

    assert tomllib.loads(command[2]) == {
        "otel": {
            "span_attributes": {
                "agent.project.id": 'project-"quoted"\\path',
                "agent.project.name": "Line\nbreak",
                "agent.project.root": "/workspace/introspection",
                "agent.project.kind": "non_git",
            }
        }
    }

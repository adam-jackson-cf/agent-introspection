from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from agent_introspection.identities import ProjectIdentity
from agent_introspection.legacy_mapping import (
    LegacyObservation,
    build_legacy_mapping_manifest,
)
from agent_introspection.project_evidence import WorkspaceResolution


class _Resolver:
    def __init__(self, workspaces: dict[str, WorkspaceResolution]) -> None:
        self.workspaces = workspaces
        self.calls: list[str] = []

    def resolve(self, workspace: str) -> WorkspaceResolution:
        self.calls.append(workspace)
        return self.workspaces.get(workspace, WorkspaceResolution("unresolved"))


def _jsonl(root: Path, rows: list[dict[str, object]]) -> Path:
    path = root / "sessions.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def _resolver(tmp_path: Path, cwd: str) -> _Resolver:
    project = tmp_path / "repo"
    project.mkdir(exist_ok=True)
    identity = ProjectIdentity("git", project, "project-id", "repo")
    return _Resolver({cwd: WorkspaceResolution("project", workspace=project, project=identity)})


def test_manifest_is_deterministic_and_filters_unsafe_json_fields(tmp_path: Path) -> None:
    cwd = str(tmp_path / "work")
    _jsonl(
        tmp_path,
        [
            {
                "type": "session_meta",
                "payload": {
                    "id": "session",
                    "timestamp": 10,
                    "cwd": cwd,
                    "originator": "codex-tui",
                    "prompt": "secret",
                    "response": "secret",
                },
            },
            {
                "type": "event_msg",
                "timestamp": "2026-01-01T00:00:00Z",
                "payload": {
                    "type": "task_started",
                    "trace_id": "trace",
                    "turn_id": "turn",
                    "started_at": 10,
                    "content": "secret",
                    "tool_payload": {"token": "secret"},
                },
            },
        ],
    )
    observations = (
        LegacyObservation("direct", "session", 11, ("log",), ("evidence",)),
        LegacyObservation("episode", "trace", 11, ("span",), ("evidence",), "episode"),
    )
    first = build_legacy_mapping_manifest(
        observations,
        codex_jsonl_roots=(tmp_path,),
        project_roots=(tmp_path,),
        created_at="2026-01-01T00:00:00Z",
        resolver=_resolver(tmp_path, cwd),
    )
    second = build_legacy_mapping_manifest(
        observations,
        codex_jsonl_roots=(tmp_path,),
        project_roots=(tmp_path,),
        created_at="2026-01-01T00:00:00Z",
        resolver=_resolver(tmp_path, cwd),
    )
    assert (
        json.dumps(asdict(first), sort_keys=True, separators=(",", ":")).encode()
        == json.dumps(
            asdict(second),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    rendered = json.dumps(
        {
            "rows": [row.__dict__ if hasattr(row, "__dict__") else str(row) for row in first.rows],
            "checksum": first.checksum,
        }
    )
    assert "secret" not in rendered
    assert [row.producer_surface for row in first.rows] == ["codex-cli", "codex-cli"]
    assert first.accepted == 2 and first.denominator == (
        first.accepted + first.rejected + first.unresolved
    )
    assert [row.correlation_id for row in first.rows] == ["session", "session"]


def test_thread_conversation_and_workspace_transition_use_exact_session_and_latest_workspace(
    tmp_path: Path,
) -> None:
    old = str(tmp_path / "old")
    latest = str(tmp_path / "latest")
    _jsonl(
        tmp_path,
        [
            {
                "type": "session_meta",
                "payload": {
                    "session_id": "shared",
                    "timestamp": 10,
                    "cwd": old,
                    "originator": "Codex Desktop",
                },
            },
            {
                "type": "turn_context",
                "payload": {"session_id": "shared", "timestamp": 20, "cwd": latest},
            },
        ],
    )
    resolver = _resolver(tmp_path, latest)
    observations = (
        LegacyObservation("thread", "shared", 21, (), (), "thread"),
        LegacyObservation("conversation", "shared", 21, (), (), "conversation"),
    )
    manifest = build_legacy_mapping_manifest(
        observations,
        codex_jsonl_roots=(tmp_path,),
        project_roots=(tmp_path,),
        created_at="at",
        resolver=resolver,
    )
    assert all(row.status == "accepted" for row in manifest.rows)
    assert all(
        row.producer == "codex-app-server" and row.producer_surface == "codex-app"
        for row in manifest.rows
    )


def test_conflicts_and_missing_or_non_git_context_fail_closed(tmp_path: Path) -> None:
    cwd = str(tmp_path / "work")
    _jsonl(
        tmp_path,
        [
            {
                "type": "session_meta",
                "payload": {
                    "id": "conflict",
                    "timestamp": 1,
                    "cwd": cwd,
                    "originator": "codex-tui",
                },
            },
            {
                "type": "session_meta",
                "payload": {
                    "id": "conflict",
                    "timestamp": 2,
                    "cwd": cwd,
                    "originator": "codex-tui",
                },
            },
            {
                "type": "session_meta",
                "payload": {
                    "id": "no-workspace",
                    "timestamp": 10,
                    "originator": "codex-tui",
                },
            },
            {
                "type": "session_meta",
                "payload": {
                    "id": "non-git",
                    "timestamp": 10,
                    "cwd": cwd,
                    "originator": "codex-tui",
                },
            },
        ],
    )
    observations = (
        LegacyObservation("conflict", "conflict", 3, (), ()),
        LegacyObservation("missing", "no-workspace", 11, (), ()),
        LegacyObservation("non-git", "non-git", 11, (), ()),
    )
    manifest = build_legacy_mapping_manifest(
        observations,
        codex_jsonl_roots=(tmp_path,),
        project_roots=(tmp_path,),
        created_at="at",
        resolver=_Resolver({cwd: WorkspaceResolution("unresolved")}),
    )
    assert [(row.status, row.reason_code) for row in manifest.rows] == [
        ("rejected", "duplicate_conflict"),
        ("unresolved", "missing_workspace"),
        ("unresolved", "invalid_workspace"),
    ]
    assert manifest.denominator == manifest.accepted + manifest.rejected + manifest.unresolved
    assert [row.correlation_id for row in manifest.rows] == [
        "conflict",
        "no-workspace",
        "non-git",
    ]


def test_missing_authoritative_linkage_remains_visible_with_null_correlation(
    tmp_path: Path,
) -> None:
    observations = (
        LegacyObservation("missing-artifact", None, 11, ("artifact",), ("evidence",)),
        LegacyObservation(
            "missing-session",
            None,
            11,
            ("artifact",),
            ("evidence",),
            "episode",
        ),
    )
    first = build_legacy_mapping_manifest(
        observations,
        codex_jsonl_roots=(tmp_path,),
        project_roots=(tmp_path,),
        created_at="at",
        resolver=_Resolver({}),
    )
    second = build_legacy_mapping_manifest(
        tuple(reversed(observations)),
        codex_jsonl_roots=(tmp_path,),
        project_roots=(tmp_path,),
        created_at="at",
        resolver=_Resolver({}),
    )
    assert [
        (row.observation_id, row.correlation_id, row.status, row.reason_code) for row in first.rows
    ] == [
        ("missing-artifact", None, "unresolved", "missing_correlation_id"),
        ("missing-session", None, "unresolved", "missing_correlation_id"),
    ]
    assert first.accepted == 0
    assert first.denominator == 2
    assert first.denominator == first.rejected + first.unresolved
    assert json.dumps(asdict(first), sort_keys=True, separators=(",", ":")) == json.dumps(
        asdict(second),
        sort_keys=True,
        separators=(",", ":"),
    )


def test_conflicting_duplicates_reject_before_resolution(
    tmp_path: Path,
) -> None:
    cwd = str(tmp_path / "work")
    identical = {
        "type": "session_meta",
        "payload": {"id": "idempotent", "timestamp": 10, "cwd": cwd, "originator": "codex-tui"},
    }
    _jsonl(
        tmp_path,
        [
            identical,
            identical,
            {
                "type": "session_meta",
                "payload": {
                    "id": "conflicting",
                    "timestamp": 10,
                    "cwd": cwd,
                    "originator": "codex-tui",
                },
            },
            {
                "type": "session_meta",
                "payload": {
                    "id": "conflicting",
                    "timestamp": 11,
                    "cwd": str(tmp_path / "other"),
                    "originator": "codex-tui",
                },
            },
            {
                "type": "event_msg",
                "timestamp": "2026-01-01T00:00:00Z",
                "payload": {
                    "type": "task_started",
                    "trace_id": "conflicting-trace",
                    "turn_id": "turn",
                    "started_at": 11,
                },
            },
            {
                "type": "event_msg",
                "timestamp": "2026-01-01T00:00:01Z",
                "payload": {
                    "type": "task_started",
                    "trace_id": "conflicting-trace",
                    "turn_id": "turn",
                    "started_at": 12,
                },
            },
        ],
    )
    resolver = _resolver(tmp_path, cwd)
    observations = (
        LegacyObservation("idempotent", "idempotent", 11, (), ()),
        LegacyObservation("conflicting-direct", "conflicting", 12, (), ()),
        LegacyObservation("conflicting-thread", "conflicting", 12, (), (), "thread"),
        LegacyObservation("conflicting-conversation", "conflicting", 12, (), (), "conversation"),
        LegacyObservation("conflicting-episode", "conflicting-trace", 12, (), (), "episode"),
    )
    manifest = build_legacy_mapping_manifest(
        observations,
        codex_jsonl_roots=(tmp_path,),
        project_roots=(tmp_path,),
        created_at="at",
        resolver=resolver,
    )
    assert [(row.observation_id, row.status, row.reason_code) for row in manifest.rows] == [
        ("conflicting-conversation", "rejected", "duplicate_conflict"),
        ("conflicting-direct", "rejected", "duplicate_conflict"),
        ("conflicting-episode", "rejected", "duplicate_conflict"),
        ("conflicting-thread", "rejected", "duplicate_conflict"),
        ("idempotent", "accepted", None),
    ]
    assert resolver.calls == [cwd]

import json
from pathlib import Path

import pytest

from agent_introspection.session_backfill import backfill


def _artifact(root: Path, records: list[dict[str, object]]) -> Path:
    path = root / "session.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    return path


def _records(workspace: Path, target: str = "src/feature.py") -> list[dict[str, object]]:
    return [
        {
            "type": "session",
            "id": "session-1",
            "timestamp": "2026-07-01T12:00:00Z",
            "cwd": str(workspace),
        },
        {
            "timestamp": "2026-07-01T12:01:00Z",
            "name": "write",
            "arguments": {"path": target, "content": "secret"},
        },
        {
            "timestamp": "2026-07-01T12:02:00Z",
            "name": "edit",
            "arguments": {"path": target, "content": "secret"},
        },
    ]


def _roots(tmp_path: Path) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    (project / "src").mkdir()
    source = tmp_path / "omp"
    inbox = tmp_path / "inbox"
    return project, source, inbox


def test_backfill_spools_repeated_non_scaffold_target_without_content_dependency(
    tmp_path: Path,
) -> None:
    project, source, inbox = _roots(tmp_path)
    _artifact(source, _records(project))

    result = backfill(roots={"omp": (source,)}, inbox=inbox)

    assert result["eligible"] == result["spooled"] == 1
    payload = json.loads(next(inbox.glob("*.json")).read_text())
    assert payload["producer"] == "omp"
    assert payload["agent"]["project"]["root"] == str(project)
    assert "secret" not in next(inbox.glob("*.json")).read_text()


@pytest.mark.parametrize(
    ("records", "reason"),
    [
        (lambda project: _records(project)[:2], "insufficient_target_consensus"),
        (lambda project: _records(project, "README.md"), "no_non_scaffold_write_target"),
        (
            lambda project: [
                {
                    "type": "session",
                    "id": "session-1",
                    "timestamp": "not-a-time",
                    "cwd": str(project),
                },
                *_records(project)[1:],
            ],
            "malformed_or_missing_metadata",
        ),
    ],
)
def test_backfill_rejects_insufficient_or_invalid_evidence(
    tmp_path: Path, records: object, reason: str
) -> None:
    project, source, inbox = _roots(tmp_path)
    _artifact(source, records(project))  # type: ignore[operator]

    result = backfill(roots={"omp": (source,)}, inbox=inbox)

    assert result["eligible"] == 0
    assert result["rejections"][reason] == 1


def test_backfill_rejects_cross_project_roots(tmp_path: Path) -> None:
    project, source, inbox = _roots(tmp_path)
    other = tmp_path / "other"
    (other / ".git").mkdir(parents=True)
    records = _records(project)
    records[1]["cwd"] = str(other)
    _artifact(source, records)

    result = backfill(roots={"omp": (source,)}, inbox=inbox)

    assert result["eligible"] == 0
    assert result["rejections"]["multiple_project_roots"] == 1


def test_backfill_is_idempotent(tmp_path: Path) -> None:
    project, source, inbox = _roots(tmp_path)
    _artifact(source, _records(project))

    first = backfill(roots={"omp": (source,)}, inbox=inbox)
    second = backfill(roots={"omp": (source,)}, inbox=inbox)

    assert first["spooled"] == 1
    assert second["spooled"] == 0
    assert len(list(inbox.glob("*.json"))) == 1

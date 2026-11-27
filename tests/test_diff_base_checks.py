from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.support.script_loader import SCRIPTS

pytestmark = pytest.mark.boundary

PROOFS = """schema = 1

[scope]
mode = "whole"
base_ref = "main"

[layout]
support_dir = "tests/support"
fixtures_dir = "tests/fixtures"
test_file_patterns = ["tests/test_*.py"]

[identity_artefacts]
"schema.sql" = "store schema"

[golden]
globs = ["tests/golden.json"]
change_record = "docs/golden-changes.md"
"""
PYPROJECT = "[tool.coverage.report]\nfail_under = 85\n"
PRAGMA = "# pragma" + ": no cover"


def _run(repo: Path, *command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env={
            **{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )


def _git(repo: Path, *args: str) -> None:
    completed = _run(repo, "git", "-c", "user.name=t", "-c", "user.email=t@localhost", *args)
    assert completed.returncode == 0, completed.stderr


def _write(repo: Path, path: str, text: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _check(repo: Path, script: str, event: str) -> subprocess.CompletedProcess[str]:
    return _run(repo, sys.executable, str(SCRIPTS / script), "--repo", ".", event)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _write(root, "proofs.toml", PROOFS)
    _write(root, "pyproject.toml", PYPROJECT)
    _write(root, "schema.sql", "create table t (id int);\n")
    _write(root, "src/app.py", "VALUE = 1\n")
    _write(root, "tests/test_app.py", "def test_value_is_one():\n    assert 1 == 1\n")
    _write(root, "tests/golden.json", "{}\n")
    _write(root, "docs/golden-changes.md", "# Golden changes\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "checkout", "-q", "-b", "feature")
    return root


def test_a_staged_pragma_fails_the_commit_and_an_unstaged_one_waits(repo: Path) -> None:
    _write(repo, "src/app.py", f"VALUE = 1  {PRAGMA}\n")

    assert _check(repo, "check-coverage-ratchet.py", "--staged").returncode == 0
    _git(repo, "add", "-A")
    staged = _check(repo, "check-coverage-ratchet.py", "--staged")

    assert staged.returncode == 1
    assert "src/app.py:1: TEST.coverage-ratchet" in staged.stderr


def test_a_committed_floor_drop_fails_against_the_merge_base(repo: Path) -> None:
    _write(repo, "pyproject.toml", PYPROJECT.replace("85", "80"))
    _git(repo, "commit", "-q", "-am", "lower the floor")

    staged = _check(repo, "check-coverage-ratchet.py", "--staged")
    merged = _check(repo, "check-coverage-ratchet.py", "--merge-base")

    assert staged.returncode == 0, staged.stderr
    assert merged.returncode == 1
    assert "fail_under lowered from 85 to 80" in merged.stderr


def test_a_staged_test_without_a_behaviour_name_fails_growth(repo: Path) -> None:
    _write(repo, "tests/test_extra.py", "def test_works():\n    assert 2 == 2\n")

    unstaged = _check(repo, "check-test-growth.py", "--staged")
    _git(repo, "add", "-A")
    staged = _check(repo, "check-test-growth.py", "--staged")
    _git(repo, "commit", "-q", "-m", "add a test")
    merged = _check(repo, "check-test-growth.py", "--merge-base")

    assert unstaged.returncode == 0, unstaged.stderr
    assert staged.returncode == 1
    assert "TEST.growth.justified" in staged.stderr
    assert merged.returncode == 1
    assert "TEST.growth.justified" in merged.stderr


def test_a_staged_test_only_change_to_an_identity_artefact_fails(repo: Path) -> None:
    _write(repo, "schema.sql", "create table t (id bigint);\n")
    _write(repo, "tests/test_app.py", "def test_value_is_one():\n    assert 1 == 1 + 0\n")
    _git(repo, "add", "-A")

    staged = _check(repo, "check-identity-artefacts.py", "--staged")

    assert staged.returncode == 1
    assert "schema.sql: TEST.identity-artefacts" in staged.stderr


def test_a_staged_golden_change_needs_a_staged_record_entry(repo: Path) -> None:
    _write(repo, "tests/golden.json", '{"a": 1}\n')
    _git(repo, "add", "-A")
    missing = _check(repo, "check-golden-changes.py", "--staged")
    _write(repo, "docs/golden-changes.md", "# Golden changes\n\n- tests/golden.json: adds a\n")
    unstaged_record = _check(repo, "check-golden-changes.py", "--staged")
    _git(repo, "add", "-A")
    recorded = _check(repo, "check-golden-changes.py", "--staged")

    assert missing.returncode == 1
    assert "tests/golden.json: TEST.golden-change-record" in missing.stderr
    assert unstaged_record.returncode == 1
    assert recorded.returncode == 0, recorded.stderr

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tests.support.script_loader import REPO_ROOT

pytestmark = pytest.mark.boundary

_SCRATCH_GIT_TESTS = (
    "tests/test_code_integrity_git_modes.py",
    "tests/test_structure_rules_scenarios.py",
    "tests/test_tier_guards.py",
    "tests/test_diff_base_checks.py",
)


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=600, check=False)


def _git(cwd: Path, *args: str) -> str:
    completed = _run(["git", "-c", "user.name=t", "-c", "user.email=t@localhost", *args], cwd)
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def _without_heads(listing: str) -> list[str]:
    return [line for line in listing.splitlines() if not line.startswith("HEAD ")]


def _state(main: Path) -> dict[str, str]:
    return {
        "refs": _git(main, "for-each-ref", "--format=%(refname) %(objectname)"),
        "config": _git(main, "config", "--list", "--local"),
        "worktrees": _git(main, "worktree", "list", "--porcelain"),
    }


def test_a_linked_worktree_commit_leaves_the_repository_refs_and_config_unchanged(
    tmp_path: Path,
) -> None:
    main, linked = tmp_path / "main", tmp_path / "linked"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    _git(main, "commit", "-q", "--allow-empty", "-m", "base")
    _git(main, "worktree", "add", "-q", "-b", "feature", str(linked))
    hook = main / ".git" / "hooks" / "pre-commit"
    targets = " ".join(_SCRATCH_GIT_TESTS)
    hook.write_text(
        '#!/bin/sh\n[ -z "$WORKTREE_HOOK_PROBE" ] || exit 0\nexport WORKTREE_HOOK_PROBE=1\n'
        f'cd "{REPO_ROOT}" || exit 1\n'
        f'exec "{sys.executable}" -m pytest -q -p no:cacheprovider -m boundary {targets}\n',
        encoding="utf-8",
    )
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
    before = _state(main)
    feature = _git(main, "rev-parse", "feature").strip()

    commit = _run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@localhost",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "probe",
        ],
        linked,
    )

    assert commit.returncode == 0, commit.stdout[-3000:] + commit.stderr[-3000:]
    assert " passed" in commit.stdout + commit.stderr
    after = _state(main)
    head = _git(main, "rev-parse", "feature").strip()
    assert _git(main, "log", "--format=%P %s", "-1", "feature").split() == [feature, "probe"]
    expected_refs = before["refs"].replace(
        f"refs/heads/feature {feature}", f"refs/heads/feature {head}"
    )
    assert after["refs"] == expected_refs
    assert after["config"] == before["config"]
    assert _without_heads(after["worktrees"]) == _without_heads(before["worktrees"])
    assert _git(linked, "rev-parse", "HEAD").strip() == head
    assert _git(main, "rev-parse", "HEAD").strip() == feature
    assert "core.bare=false" in after["config"]
    assert os.environ.get("GIT_DIR") is None

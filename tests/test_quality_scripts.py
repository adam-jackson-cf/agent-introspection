from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).parents[1] / "scripts"
_PRE_COMMIT = """repos:
  - repo: local
    hooks:
      - id: quality-gates
        entry: bash scripts/run-ci-quality-gates.sh --fix --stage
        language: system
"""
_WORKFLOW = """jobs:
  quality-gates:
    steps:
      - uses: actions/checkout@v4
      - name: Run quality gates
        run: bash scripts/run-ci-quality-gates.sh
"""
_PARITY_REACHED = "parity-check-reached"


def _isolated_env() -> dict[str, str]:
    """Drop GIT_* variables so a git hook's GIT_DIR/GIT_INDEX_FILE cannot redirect us.

    The repo's pre-commit hook runs this suite; in a worktree git exports
    absolute GIT_DIR and GIT_INDEX_FILE, which would otherwise make these
    temp-repo commands rewrite the host repository's index, branch and config.
    """
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=_isolated_env(),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def _run(repo: Path, script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", f"scripts/{script}", *args],
        cwd=repo,
        env=_isolated_env(),
        capture_output=True,
        text=True,
        check=False,
    )


def _make_staging_repo(tmp_path: Path) -> Path:
    (tmp_path / "scripts").mkdir(parents=True)
    shutil.copy(_SCRIPTS / "run-ci-quality-gates.sh", tmp_path / "scripts")
    (tmp_path / "scripts/check-quality-gate-parity.sh").write_text(
        f"echo {_PARITY_REACHED}\nexit 7\n"
    )
    (tmp_path / "module.py").write_text("x = 1\n")
    (tmp_path / "notes.txt").write_text("a\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "test")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


@pytest.fixture
def staging_repo(tmp_path: Path) -> Path:
    """A git repo holding the gate runner, with the rest of the suite stubbed out."""
    return _make_staging_repo(tmp_path)


def test_git_hook_environment_does_not_leak_into_temp_repos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = tmp_path / "host"
    host.mkdir()
    (host / "tracked.txt").write_text("host\n")
    _git(host, "init", "-q")
    _git(host, "config", "user.email", "host@example.com")
    _git(host, "config", "user.name", "host")
    _git(host, "add", "-A")
    _git(host, "commit", "-qm", "host")
    head_before = _git(host, "rev-parse", "HEAD")
    # Mimic what git exports to hooks when committing from a worktree.
    monkeypatch.setenv("GIT_DIR", str(host / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(host / ".git/index"))
    monkeypatch.setenv("GIT_WORK_TREE", str(host))

    repo = _make_staging_repo(tmp_path / "scratch")
    (repo / "module.py").write_text("x = 2\n")
    _run(repo, "run-ci-quality-gates.sh", "--fix", "--stage")

    assert _git(host, "rev-parse", "HEAD") == head_before
    assert _git(host, "ls-files") == "tracked.txt\n"
    assert _git(host, "config", "user.email") == "host@example.com\n"
    assert _git(repo, "rev-parse", "--show-toplevel").strip() == str(repo.resolve())


def test_stage_refuses_unstaged_lint_changes_without_touching_index(
    staging_repo: Path,
) -> None:
    (staging_repo / "module.py").write_text("x = 2\n")
    index_before = _git(staging_repo, "diff", "--cached", "--name-only")

    result = _run(staging_repo, "run-ci-quality-gates.sh", "--fix", "--stage")

    assert result.returncode == 2
    assert "unstaged changes" in result.stderr
    assert "module.py" in result.stderr
    assert _PARITY_REACHED not in result.stdout
    assert _git(staging_repo, "diff", "--cached", "--name-only") == index_before


def test_stage_proceeds_when_lint_changes_are_already_staged(staging_repo: Path) -> None:
    (staging_repo / "module.py").write_text("x = 2\n")
    _git(staging_repo, "add", "module.py")
    (staging_repo / "notes.txt").write_text("b\n")

    result = _run(staging_repo, "run-ci-quality-gates.sh", "--fix", "--stage")

    assert _PARITY_REACHED in result.stdout
    assert result.returncode == 7


def test_fix_without_stage_ignores_unstaged_changes(staging_repo: Path) -> None:
    (staging_repo / "module.py").write_text("x = 2\n")

    result = _run(staging_repo, "run-ci-quality-gates.sh", "--fix")

    assert _PARITY_REACHED in result.stdout


def _parity_repo(tmp_path: Path, pre_commit: str, workflow: str) -> Path:
    (tmp_path / "scripts").mkdir()
    shutil.copy(_SCRIPTS / "check-quality-gate-parity.sh", tmp_path / "scripts")
    (tmp_path / ".pre-commit-config.yaml").write_text(pre_commit)
    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / ".github/workflows/ci-quality-gates.yml").write_text(workflow)
    return tmp_path


def test_parity_passes_for_active_runner_invocations(tmp_path: Path) -> None:
    repo = _parity_repo(tmp_path, _PRE_COMMIT, _WORKFLOW)

    assert _run(repo, "check-quality-gate-parity.sh").returncode == 0


def test_parity_passes_for_repository_config() -> None:
    repo = Path(__file__).parents[1]

    assert _run(repo, "check-quality-gate-parity.sh").returncode == 0


@pytest.mark.parametrize(
    ("pre_commit", "workflow", "missing_file"),
    [
        (
            _PRE_COMMIT.replace("        entry:", "        # entry:"),
            _WORKFLOW,
            ".pre-commit-config.yaml",
        ),
        (
            _PRE_COMMIT.replace(
                "entry: bash scripts/run-ci-quality-gates.sh --fix --stage",
                "entry: true  # bash scripts/run-ci-quality-gates.sh",
            ),
            _WORKFLOW,
            ".pre-commit-config.yaml",
        ),
        (
            _PRE_COMMIT,
            _WORKFLOW.replace("        run:", "        # run:"),
            ".github/workflows/ci-quality-gates.yml",
        ),
        (
            _PRE_COMMIT,
            _WORKFLOW.replace(
                "        run:",
                "        # scripts/run-ci-quality-gates.sh\n        run: true\n        # run:",
            ),
            ".github/workflows/ci-quality-gates.yml",
        ),
        (
            _PRE_COMMIT.replace("entry: bash", "entry: echo"),
            _WORKFLOW,
            ".pre-commit-config.yaml",
        ),
        (
            _PRE_COMMIT,
            _WORKFLOW.replace("run: bash", "run: echo"),
            ".github/workflows/ci-quality-gates.yml",
        ),
    ],
    ids=[
        "pre-commit-entry-commented",
        "pre-commit-trailing-comment",
        "workflow-run-commented",
        "workflow-comment-line-only",
        "pre-commit-entry-only-mentions-runner",
        "workflow-run-only-mentions-runner",
    ],
)
def test_parity_rejects_runner_not_actively_invoked(
    tmp_path: Path, pre_commit: str, workflow: str, missing_file: str
) -> None:
    repo = _parity_repo(tmp_path, pre_commit, workflow)

    result = _run(repo, "check-quality-gate-parity.sh")

    assert result.returncode != 0
    assert missing_file in result.stderr

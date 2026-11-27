from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.boundary

_SCRIPTS = Path(__file__).parents[1] / "scripts"
_STUB = """#!/bin/sh
printf '%s %s\\n' "$(basename "$0")" "$*" >>"$STUB_CALLS"
case "$*" in
  *list-python-scope.py*) printf 'module.py\\000' ;;
esac
exit 0
"""


def _isolated_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=_isolated_env(),
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return completed.stdout


def _run(repo: Path, script: str, *args: str) -> subprocess.CompletedProcess[str]:
    stubs = repo.parent / f"{repo.name}-bin"
    environment = {
        **_isolated_env(),
        "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
        "STUB_CALLS": str(repo.parent / f"{repo.name}-calls"),
    }
    return subprocess.run(
        ["bash", f"scripts/{script}", *args],
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _calls(repo: Path) -> list[str]:
    log = repo.parent / f"{repo.name}-calls"
    return log.read_text().splitlines() if log.exists() else []


def _make_gate_repo(root: Path) -> Path:
    (root / "scripts").mkdir(parents=True)
    for script in ("run-ci-quality-gates.sh", "fix-quality-gates.sh"):
        shutil.copy(_SCRIPTS / script, root / "scripts")
    stubs = root.parent / f"{root.name}-bin"
    stubs.mkdir()
    for tool in ("uv", "bun"):
        stub = stubs / tool
        stub.write_text(_STUB)
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    (root / "module.py").write_text("x=1\n")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "test")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    return root


@pytest.fixture
def gate_repo(tmp_path: Path) -> Path:
    return _make_gate_repo(tmp_path / "repo")


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
    monkeypatch.setenv("GIT_DIR", str(host / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(host / ".git/index"))
    monkeypatch.setenv("GIT_WORK_TREE", str(host))

    repo = _make_gate_repo(tmp_path / "scratch")
    result = _run(repo, "run-ci-quality-gates.sh", "check", "ruff_format", "module.py")

    assert result.returncode == 0, result.stderr
    assert _calls(repo) == ["uv run ruff format --check --no-cache module.py"]
    assert _git(host, "rev-parse", "HEAD") == head_before
    assert _git(host, "ls-files") == "tracked.txt\n"
    assert _git(host, "config", "user.email") == "host@example.com\n"
    assert _git(repo, "rev-parse", "--show-toplevel").strip() == str(repo.resolve())


@pytest.mark.parametrize("flag", ["--fix", "--stage"])
def test_the_gate_entrypoint_rejects_the_removed_fix_and_stage_flags(
    gate_repo: Path, flag: str
) -> None:
    result = _run(gate_repo, "run-ci-quality-gates.sh", flag)

    assert result.returncode == 2
    assert "Usage:" in result.stderr
    assert _calls(gate_repo) == []
    assert _git(gate_repo, "status", "--porcelain") == ""


def test_a_named_check_only_reports_and_leaves_the_tree_unchanged(gate_repo: Path) -> None:
    result = _run(gate_repo, "run-ci-quality-gates.sh", "check", "ruff", "module.py")

    assert result.returncode == 0, result.stderr
    assert _calls(gate_repo) == ["uv run ruff check --no-fix --no-cache module.py"]
    assert _git(gate_repo, "status", "--porcelain") == ""


def test_the_fix_script_is_the_only_route_that_rewrites_files(gate_repo: Path) -> None:
    result = _run(gate_repo, "fix-quality-gates.sh")

    assert result.returncode == 0, result.stderr
    assert _calls(gate_repo) == [
        "uv run python scripts/list-python-scope.py",
        "uv run ruff format --force-exclude module.py",
        "uv run ruff check --fix --force-exclude module.py",
        "bun run --cwd dashboard format",
    ]

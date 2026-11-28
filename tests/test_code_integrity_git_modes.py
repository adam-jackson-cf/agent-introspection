from __future__ import annotations

import subprocess
from pathlib import Path
from types import ModuleType

import pytest

from tests.support.script_loader import load_script

pytestmark = pytest.mark.boundary

_LAYOUT = (
    '[layout]\nsupport_dir = "tests/support"\nfixtures_dir = "tests/fixtures"\n'
    'test_file_patterns = ["tests/test_*.py", "web/**/*.test.ts"]\n'
)


@pytest.fixture(scope="module")
def checker() -> ModuleType:
    return load_script("check-code-integrity")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "proofs.toml").write_text(_LAYOUT, encoding="utf-8")
    return tmp_path


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@localhost", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        timeout=30,
    )


@pytest.fixture
def history(repo: Path) -> Path:
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.py").write_text("x = 1  # old\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def test_staged_mode_reports_only_staged_added_lines(
    checker: ModuleType,
    history: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(history)
    (history / "a.py").write_text("x = 1  # old\ny = 2  # new\n", encoding="utf-8")
    assert checker.main(["--staged"]) == 0
    _git(history, "add", "a.py")
    assert checker.main(["--staged", "--", "."]) == 1
    errors = capsys.readouterr().err
    assert "a.py:2: CODE.no-comments: comment '# new'" in errors
    assert "a.py:1:" not in errors


def test_base_mode_reports_lines_added_since_the_merge_base(
    checker: ModuleType,
    history: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(history)
    _git(history, "checkout", "-q", "-b", "topic")
    (history / "b.py").write_text("def f() -> None:\n    pass\n", encoding="utf-8")
    _git(history, "add", "b.py")
    _git(history, "commit", "-q", "-m", "topic")
    assert checker.main(["--base", "main"]) == 1
    errors = capsys.readouterr().err
    assert "b.py:1: CODE.no-placeholder-bodies" in errors
    assert "a.py" not in errors
    assert checker.main(["--base", "main", "--", "a.py"]) == 0


def test_all_mode_scans_every_tracked_file_the_pathspec_selects(
    checker: ModuleType,
    history: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(history)
    assert checker.main(["--all", "--", ".", ":(exclude)a.py"]) == 0
    assert checker.main(["--all"]) == 1
    assert "a.py:1: CODE.no-comments: comment '# old'" in capsys.readouterr().err
    assert checker.main([]) == 2

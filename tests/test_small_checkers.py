"""Regression tests for the small quality-gate checker scripts."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.script_loader import load_script as _load


def test_timeout_none_counts_as_missing(tmp_path: Path) -> None:
    checker = _load("check-test-subprocess-timeouts")
    source = tmp_path / "test_x.py"
    source.write_text(
        "import subprocess\n"
        "subprocess.run(['a'], timeout=None)\n"
        "subprocess.run(['a'], timeout=5)\n",
        encoding="utf-8",
    )
    problems = checker.check_file(source)
    assert len(problems) == 1
    assert ":2:" in problems[0]


def _lock_fixture(tmp_path: Path, specifier: str) -> Path:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "demo"\nversion = "1.0"\ndependencies = ["requests>=2.0"]\n',
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "demo"\nversion = "1.0"\n'
        "[package.metadata]\n"
        f'requires-dist = [{{ name = "requests", specifier = "{specifier}" }}]\n',
        encoding="utf-8",
    )
    return pyproject


def test_lock_parity_detects_specifier_change(tmp_path: Path) -> None:
    checker = _load("check-lock-parity")
    assert checker.check_python(_lock_fixture(tmp_path, ">=2.0")) == []
    problems = checker.check_python(_lock_fixture(tmp_path, ">=1.0"))
    assert len(problems) == 1
    assert "dependency drift" in problems[0]


def test_lock_parity_detects_marker_change(tmp_path: Path) -> None:
    checker = _load("check-lock-parity")
    pyproject = _lock_fixture(tmp_path, ">=2.0")
    pyproject.write_text(
        '[project]\nname = "demo"\nversion = "1.0"\n'
        "dependencies = [\"requests>=2.0; python_version >= '3.13'\"]\n",
        encoding="utf-8",
    )
    problems = checker.check_python(pyproject)
    assert len(problems) == 1
    assert "dependency drift" in problems[0]
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "demo"\nversion = "1.0"\n'
        "[package.metadata]\n"
        'requires-dist = [{ name = "requests", specifier = ">=2.0", '
        "marker = \"python_version >= '3.13'\" }]\n",
        encoding="utf-8",
    )
    assert checker.check_python(pyproject) == []


def test_mypy_coverage_accepts_dot_dot_slash_and_globs(tmp_path: Path) -> None:
    checker = _load("check-python-mypy")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/a.py").write_text("", encoding="utf-8")
    (tmp_path / "b.py").write_text("", encoding="utf-8")
    for files in ('["."]', '["./pkg", "*.py"]'):
        config = tmp_path / "pyproject.toml"
        config.write_text(f"[tool.mypy]\nfiles = {files}\n", encoding="utf-8")
        assert checker.coverage_problems(tmp_path, config) == []


def test_skill_metadata_uses_real_yaml(tmp_path: Path) -> None:
    checker = _load("check-skill-metadata")
    skill = tmp_path / "demo" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text(
        "---\nname: demo\ndescription: Use when testing\nglobal: true\n---\nbody\n",
        encoding="utf-8",
    )
    assert checker.check_skill(skill) == []
    skill.write_text(
        "---\nname: demo\ndescription: Use when testing\nbroken: [unclosed\n---\nbody\n",
        encoding="utf-8",
    )
    assert any("invalid YAML" in problem for problem in checker.check_skill(skill))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import subprocess\nsubprocess.run(['a'], timeout=0)\n", "positive timeout"),
        ("import subprocess\nsubprocess.run(['a'], timeout=-1)\n", "positive timeout"),
        ("import os\nos.system('true')\n", "os.system has no bound"),
        (
            "import asyncio\nasync def go() -> None:\n"
            "    await asyncio.create_subprocess_exec('true')\n",
            "asyncio.wait_for",
        ),
    ],
)
def test_an_unbounded_or_non_positive_process_call_is_reported(
    tmp_path: Path, source: str, expected: str
) -> None:
    checker = _load("check-test-subprocess-timeouts")
    path = tmp_path / "test_x.py"
    path.write_text(source, encoding="utf-8")
    (problem,) = checker.check_file(path)
    assert "TEST.subprocess-timeouts" in problem
    assert expected in problem


def test_a_bounded_async_process_wait_is_accepted(tmp_path: Path) -> None:
    checker = _load("check-test-subprocess-timeouts")
    path = tmp_path / "test_x.py"
    path.write_text(
        "import asyncio\nasync def go() -> None:\n"
        "    await asyncio.wait_for(asyncio.create_subprocess_exec('true'), 5)\n",
        encoding="utf-8",
    )
    assert checker.check_file(path) == []


@pytest.mark.parametrize(
    ("name", "problems"),
    [("passing/bounded.test.ts", 0), ("violating/unbounded.test.ts", 1)],
)
def test_a_bun_process_call_needs_a_timeout_option(name: str, problems: int) -> None:
    checker = _load("check-test-subprocess-timeouts")
    path = Path(__file__).parent / "fixtures" / "proofs" / "tests.subprocess-timeouts" / name
    assert len(checker.check_file(path)) == problems

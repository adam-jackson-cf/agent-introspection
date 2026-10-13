"""Regression tests for the small quality-gate checker scripts."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPTS = Path(__file__).parents[1] / "scripts"


def _load(name: str) -> ModuleType:
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), _SCRIPTS / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_render_measures_handles_missing_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _load("render-measures")
    monkeypatch.setattr(script, "DOC", tmp_path / "measure.md")
    monkeypatch.setattr(script, "GAPS", tmp_path / "gaps.md")
    monkeypatch.setattr(script, "render_gaps", lambda: "gaps")
    monkeypatch.setattr(script, "updated", lambda _text, content=None: f"rendered {content}")
    assert script.main(["--check"]) == 1
    assert "out of date" in capsys.readouterr().out
    assert script.main([]) == 0
    assert (tmp_path / "gaps.md").exists()
    assert script.main(["--check"]) == 0

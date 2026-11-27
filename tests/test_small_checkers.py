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


def test_suppressions_report_each_comment_with_its_line() -> None:
    checker = _load("check-python-suppressions")
    source = 'import os  # noqa: F401\nTEXT = "# noqa"\nvalue = 1  # type: ignore[assignment]\n'
    assert checker.comment_findings("src/a.py", source) == [
        "src/a.py:1: RUFF.IGNORES: suppression comment '# noqa: F401'; "
        "fix the finding and delete the comment",
        "src/a.py:3: RUFF.IGNORES: suppression comment '# type: ignore[assignment]'; "
        "fix the finding and delete the comment",
    ]


def test_suppressions_fail_on_source_that_cannot_be_tokenized() -> None:
    checker = _load("check-python-suppressions")
    [finding] = checker.comment_findings("src/a.py", 'TEXT = """open\n')
    assert finding.startswith("src/a.py: RUFF.IGNORES: cannot be tokenized")


def _suppression_repo(tmp_path: Path, settings: str) -> Path:
    (tmp_path / "src/pkg").mkdir(parents=True)
    (tmp_path / "src/pkg/module.py").write_text("VALUE = 1\n", encoding="utf-8")
    config = tmp_path / "pyproject.toml"
    config.write_text(settings, encoding="utf-8")
    return config


def test_suppressions_reject_every_ignore_setting(tmp_path: Path) -> None:
    checker = _load("check-python-suppressions")
    config = _suppression_repo(
        tmp_path,
        '[tool.ruff.lint]\nignore = ["E501"]\n\n'
        '[tool.ruff.lint.per-file-ignores]\n"tests/**" = ["S101"]\n\n'
        '[[tool.mypy.overrides]]\nmodule = ["yaml"]\nignore_missing_imports = true\n',
    )
    assert checker.repository_findings(config) == [
        "pyproject.toml:2: RUFF.IGNORES: [tool.ruff.lint] ignore lists E501; "
        "fix the findings it hides and delete the entry",
        'pyproject.toml:5: RUFF.IGNORES: [tool.ruff.lint.per-file-ignores] "tests/**" ignores '
        "S101; fix the findings it hides and delete the entry",
        "pyproject.toml:9: RUFF.IGNORES: [[tool.mypy.overrides]] module ['yaml'] sets "
        "ignore_missing_imports = True; fix the findings it hides and delete the entry",
    ]


def test_suppressions_reject_an_exclude_that_hides_maintained_code(tmp_path: Path) -> None:
    checker = _load("check-python-suppressions")
    config = _suppression_repo(
        tmp_path, '[tool.ruff]\nexclude = [".venv", "tests/fixtures/", "src/pkg"]\n'
    )
    assert checker.repository_findings(config) == [
        'pyproject.toml:2: RUFF.ROOT.exclude: [tool.ruff] exclude "src/pkg" hides maintained '
        "file src/pkg/module.py; narrow the pattern to non-maintained paths"
    ]


@pytest.mark.parametrize(
    ("pattern", "relpath", "matches"),
    [
        ("node_modules", "src/node_modules/a.py", True),
        ("*.egg-info", "src/pkg.egg-info/a.py", True),
        ("tests/fixtures/", "tests/fixtures/x/a.py", True),
        ("tests/fixtures/", "tests/test_fixtures.py", False),
        ("./src/pkg", "src/pkg/module.py", True),
        ("build", "src/builder.py", False),
    ],
)
def test_ruff_exclude_patterns_match_like_ruff(pattern: str, relpath: str, matches: bool) -> None:
    checker = _load("check-python-suppressions")
    assert checker.ruff_pattern_matches(pattern, relpath) is matches


def test_suppressions_pass_a_clean_repository_and_check_named_files(tmp_path: Path) -> None:
    checker = _load("check-python-suppressions")
    config = _suppression_repo(tmp_path, '[tool.ruff]\nexclude = [".venv"]\n')
    named = tmp_path / "named.py"
    named.write_text("import os  # ruff: noqa\n", encoding="utf-8")
    assert checker.main(["--config", str(config)]) == 0
    assert checker.main([str(named)]) == 1


def test_mypy_exclude_must_not_hide_maintained_code(tmp_path: Path) -> None:
    checker = _load("check-python-mypy")
    (tmp_path / "src/pkg").mkdir(parents=True)
    (tmp_path / "src/pkg/module.py").write_text("", encoding="utf-8")
    config = tmp_path / "pyproject.toml"
    config.write_text('[tool.mypy]\nfiles = ["src"]\nexclude = ["^src/pkg/"]\n', encoding="utf-8")
    assert checker.coverage_problems(tmp_path, config) == [
        "pyproject.toml: MYPY.SETTING.exclude: [tool.mypy] exclude '^src/pkg/' hides "
        "maintained file src/pkg/module.py; narrow the pattern to non-maintained paths"
    ]


def test_mypy_reports_an_uncovered_maintained_file(tmp_path: Path) -> None:
    checker = _load("check-python-mypy")
    (tmp_path / "src").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/tool.py").write_text("", encoding="utf-8")
    config = tmp_path / "pyproject.toml"
    config.write_text('[tool.mypy]\nfiles = ["src"]\n', encoding="utf-8")
    assert checker.coverage_problems(tmp_path, config) == [
        "scripts/tool.py: MYPY.package-coverage: not covered by [tool.mypy] files/packages; "
        "add its source root to [tool.mypy] files"
    ]


def test_skill_metadata_uses_real_yaml(tmp_path: Path) -> None:
    checker = _load("check-skill-metadata")
    schema = checker.load_schema(
        Path(__file__).parents[1] / ".agents" / "skill-metadata.schema.json"
    )
    skill = tmp_path / "demo" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text(
        "---\nname: demo\ndescription: Use when testing\nglobal: true\n---\nbody\n",
        encoding="utf-8",
    )
    assert checker.check_skill(skill, schema) == []
    skill.write_text(
        "---\nname: demo\ndescription: Use when testing\nbroken: [unclosed\n---\nbody\n",
        encoding="utf-8",
    )
    assert any("invalid YAML" in problem for problem in checker.check_skill(skill, schema))


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

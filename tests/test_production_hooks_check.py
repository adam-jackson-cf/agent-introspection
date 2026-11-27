from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.script_loader import CANARIES, load_script

checker = load_script("check-test-hooks")
CASES = CANARIES / "tests.hooks"
MARKERS = frozenset({"pytest", "test", "testing", "tests", "vitest", "jest"})


def _scope(root: Path, production: list[str], tests: list[str]) -> object:
    return checker.Scope(root, production, tests, production, MARKERS)


@pytest.mark.parametrize(
    ("relative", "problem"),
    [
        ("violating/test_marker_branch.py", "reads the test marker PYTEST_CURRENT_TEST"),
        ("cases/test_hook_parameter.py", "declares the test-only parameter for_testing"),
        ("cases/loaded_test_module.py", "reads the test marker pytest from the loaded module"),
    ],
)
def test_a_production_test_hook_is_reported(relative: str, problem: str) -> None:
    (finding,) = checker.python_findings(_scope(CASES, [relative], []), relative)
    assert "TEST.no-test-hooks" in finding
    assert problem in finding


def test_production_that_reads_real_configuration_is_clean() -> None:
    relative = "passing/real_configuration.py"
    assert checker.python_findings(_scope(CASES, [relative], []), relative) == []


def test_a_bun_environment_branch_on_the_test_runner_is_reported(tmp_path: Path) -> None:
    (tmp_path / "server.ts").write_text(
        'const port = process.env.NODE_ENV === "test" ? 0 : 4173;\n', encoding="utf-8"
    )
    (finding,) = checker.typescript_findings(_scope(tmp_path, ["server.ts"], []), "server.ts")
    assert "NODE_ENV" in finding


def test_a_symbol_only_tests_reference_is_reported(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        "def used() -> int:\n    return 1\n\n\ndef only_tested() -> int:\n    return used()\n",
        encoding="utf-8",
    )
    (tmp_path / "main.py").write_text("from app import used\n\nused()\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text(
        "from app import only_tested\n\n\ndef test_x() -> None:\n    only_tested()\n",
        encoding="utf-8",
    )
    scope = checker.Scope(
        tmp_path, ["app.py", "main.py"], ["test_app.py"], ["app.py", "main.py"], MARKERS
    )
    (finding,) = checker.test_only_findings(scope)
    assert finding.startswith("app.py:5: TEST.no-test-hooks: only_tested")

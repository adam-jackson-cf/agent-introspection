from __future__ import annotations

import pytest

from tests.support.script_loader import CANARIES, REPO_ROOT, load_script

checker = load_script("check-test-placement")
LAYOUT = checker.load_layout(REPO_ROOT)


@pytest.mark.parametrize(
    ("path", "corrected"),
    [
        ("tests/test_spool_batches_v2.py", "tests/test_spool_batches.py"),
        ("tests/test_spool_2026.py", "tests/test_spool.py"),
        ("tests/test_spool_issue42.py", "tests/test_spool.py"),
        ("dashboard/src/charts-v3.test.ts", "dashboard/src/charts.test.ts"),
    ],
)
def test_a_ticket_version_or_date_token_is_reported_with_the_corrected_name(
    path: str, corrected: str
) -> None:
    (problem,) = checker.naming_problems(LAYOUT, path)
    assert "NAME.test-files" in problem
    assert problem.endswith(corrected)


def test_a_behaviour_name_with_an_allowlisted_number_is_clean() -> None:
    assert checker.naming_problems(LAYOUT, "tests/test_spool_utf8_records.py") == []


def test_a_test_like_file_outside_the_declared_patterns_is_reported() -> None:
    (problem,) = checker.naming_problems(LAYOUT, "tests/tests_spool.py")
    assert "matches no [layout] test_file_patterns" in problem


@pytest.mark.parametrize(
    ("path", "destination"),
    [
        ("tests/spool_cases.py", "move shared code to tests/support/"),
        ("tests/support/test_spool_batches.py", "a test file sits in the support layout"),
        ("scripts/conftest.py", "move it to tests/conftest.py"),
        ("tests/test_unowned_behaviour.py", "no [structure.ownership_areas] entry"),
    ],
)
def test_a_misplaced_or_unmapped_file_is_reported_with_its_destination(
    path: str, destination: str
) -> None:
    problems = checker.placement_problems(LAYOUT, path)
    assert any("TEST.placement" in problem and destination in problem for problem in problems)


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_spool_batches.py",
        "tests/conftest.py",
        "tests/support/otlp_collector.py",
        "tests/fixtures/guardrails/anything.txt",
        "dashboard/src/charts.test.tsx",
        "dashboard/server/test-fixtures.ts",
    ],
)
def test_a_placed_and_owned_path_is_clean(path: str) -> None:
    assert checker.placement_problems(LAYOUT, path) == []
    assert checker.naming_problems(LAYOUT, path) == []


def test_a_canary_fixture_is_judged_at_its_virtual_repository_path() -> None:
    fixture = CANARIES / "tests.placement" / "violating-name" / "repo" / "tests"
    raw = (fixture / "test_spool_batches_v2.py").relative_to(REPO_ROOT).as_posix()
    assert checker.judged_path(LAYOUT, raw) == "tests/test_spool_batches_v2.py"


def test_an_absolute_canary_path_is_judged_at_its_virtual_repository_path() -> None:
    fixture = CANARIES / "tests.placement" / "violating-name" / "repo" / "tests"
    absolute = str(fixture / "test_spool_batches_v2.py")
    assert checker.judged_path(LAYOUT, absolute) == "tests/test_spool_batches_v2.py"

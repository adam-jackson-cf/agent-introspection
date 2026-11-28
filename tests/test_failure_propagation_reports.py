from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPTS = Path(__file__).parents[1] / "scripts"


def _lib(name: str) -> ModuleType:
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    __import__(f"lib.{name}")
    return sys.modules[f"lib.{name}"]


def _script(name: str) -> ModuleType:
    _lib("run_evidence")
    module_name = name.replace("-", "_")
    spec = importlib.util.spec_from_file_location(module_name, _SCRIPTS / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _report(tmp_path: Path, outcomes: list[str], name: str = "t1.json") -> Path:
    evidence = _lib("run_evidence")
    cases = tuple(
        evidence.CaseRecord(f"tests/test_a.py::test_case_{index}", outcome, "", 0.01, 0)
        for index, outcome in enumerate(outcomes)
    )
    path = tmp_path / name
    evidence.write_report(evidence.RunReport("t1", "pytest", 1.0, 1.0, cases), path)
    return path


def _listing(tmp_path: Path, ids: list[str]) -> Path:
    evidence = _lib("run_evidence")
    path = tmp_path / "t1-listing.json"
    evidence.write_listing(evidence.TierListing("t1", "pytest", tuple(ids)), path)
    return path


def test_a_tier_whose_tests_all_ran_passes(tmp_path: Path) -> None:
    checker = _script("check-test-run")
    report = _report(tmp_path, ["passed", "skipped"])
    listing = _listing(tmp_path, ["tests/test_a.py::test_case_0", "tests/test_a.py::test_case_1"])

    assert checker.check([report], [listing]) == []


def test_a_tier_that_ran_no_tests_fails(tmp_path: Path) -> None:
    checker = _script("check-test-run")

    problems = checker.check([_report(tmp_path, [])], [])

    assert len(problems) == 1
    assert "TEST.failure-propagation: tier t1 (pytest) ran no tests" in problems[0]


def test_an_empty_collection_listing_fails(tmp_path: Path) -> None:
    checker = _script("check-test-run")

    problems = checker.check([_report(tmp_path, ["passed"])], [_listing(tmp_path, [])])

    assert problems
    assert "collected no tests" in problems[0]


def test_a_tier_where_every_test_was_skipped_fails(tmp_path: Path) -> None:
    checker = _script("check-test-run")

    problems = checker.check([_report(tmp_path, ["skipped", "xfail"])], [])

    assert any("every test in tier t1 (pytest) was skipped" in problem for problem in problems)


@pytest.mark.parametrize("outcome", ["failed", "error", "timeout"])
def test_a_broken_case_in_a_passing_run_fails(tmp_path: Path, outcome: str) -> None:
    checker = _script("check-test-run")

    problems = checker.check([_report(tmp_path, ["passed", outcome])], [])

    assert problems == [
        f"tests/test_a.py::test_case_1: TEST.failure-propagation: the run report records {outcome} "
        "but the runner passed; make the runner exit nonzero when a test fails, errors or times out"
    ]


def test_a_collected_test_missing_from_the_report_fails(tmp_path: Path) -> None:
    checker = _script("check-test-run")
    listing = _listing(tmp_path, ["tests/test_a.py::test_case_0", "tests/test_a.py::test_gone"])

    problems = checker.check([_report(tmp_path, ["passed"])], [listing])

    assert len(problems) == 1
    assert problems[0].startswith("tests/test_a.py::test_gone: TEST.failure-propagation: collected")


def test_a_missing_report_fails(tmp_path: Path) -> None:
    checker = _script("check-test-run")

    problems = checker.check([tmp_path / "absent.json"], [])

    assert len(problems) == 1
    assert "run report is missing or unreadable" in problems[0]

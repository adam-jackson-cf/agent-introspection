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


def _reports(seconds: list[float], wall: float) -> list[object]:
    evidence = _lib("run_evidence")
    cases = tuple(
        evidence.CaseRecord(f"tests/test_a.py::test_case_{index}", "passed", "", value, 0)
        for index, value in enumerate(seconds)
    )
    return [evidence.RunReport("t1", "pytest", 100.0, wall, cases)]


def _limits(checker: ModuleType) -> object:
    return checker.Limits(wall_seconds=10.0, per_test_seconds=1.0)


def test_a_run_within_the_approved_limits_passes() -> None:
    checker = _script("check-test-budget")

    assert checker.judge(_reports([0.1, 0.9], 4.0), _limits(checker), "machine", set()) == []


def test_a_run_over_the_wall_limit_names_the_rule_and_machine() -> None:
    checker = _script("check-test-budget")

    problems = checker.judge(_reports([0.1], 12.5), _limits(checker), "machine", set())

    assert problems == [
        "proofs.toml: TEST.budget.t1-time: T1 took 12.50s on machine, over the approved 10s; "
        "speed up the slowest T1 tests (a limit raise needs owner approval)"
    ]


def test_a_slow_test_gets_a_speed_up_fix_never_a_tier_move() -> None:
    checker = _script("check-test-budget")

    problems = checker.judge(_reports([0.1, 1.5], 4.0), _limits(checker), "machine", set())

    assert problems == [
        "tests/test_a.py::test_case_1: TEST.budget.per-test-time: took 1.500s on machine, over the "
        "approved 1s; speed the test up, never move it to another tier"
    ]


def test_an_approved_deferred_test_is_not_judged() -> None:
    checker = _script("check-test-budget")
    deferred = {"tests/test_a.py::test_case_1"}

    assert checker.judge(_reports([0.1, 1.5], 4.0), _limits(checker), "machine", deferred) == []


def test_machine_limits_are_read_for_the_probed_machine() -> None:
    checker = _script("check-test-budget")
    entry = {
        "id": "m1",
        "wall_limit_s": 30,
        "per_test_limit_s": 1.5,
        "approval": "owner, 2026-11-27",
    }

    limits = checker.machine_limits({"test_budget": {"machine": [entry]}}, "m1")

    assert limits == checker.Limits(30.0, 1.5)


def test_an_unknown_machine_has_no_limits() -> None:
    checker = _script("check-test-budget")

    assert checker.machine_limits({}, "m1") is None


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ({"id": "m1", "wall_limit_s": 30, "per_test_limit_s": 1}, "no approval"),
        ({"id": "m1", "wall_limit_s": -1, "per_test_limit_s": 1, "approval": "o"}, "positive"),
        ({"id": "m1", "wall_limit_s": "30", "per_test_limit_s": 1, "approval": "o"}, "number"),
    ],
)
def test_invalid_machine_limits_are_rejected(entry: dict[str, object], message: str) -> None:
    checker = _script("check-test-budget")
    settings = _lib("proofs_settings")

    with pytest.raises(settings.SettingsError, match=message):
        checker.machine_limits({"test_budget": {"machine": [entry]}}, "m1")


def test_a_stale_or_wrong_tier_report_is_rejected(tmp_path: Path) -> None:
    checker = _script("check-test-budget")
    evidence = _lib("run_evidence")
    stale = tmp_path / "t1.json"
    other = tmp_path / "t2.json"
    evidence.write_report(evidence.RunReport("t1", "pytest", 100.0, 1.0, ()), stale)
    evidence.write_report(evidence.RunReport("t2", "pytest", 300.0, 1.0, ()), other)

    reports, problems = checker._reports([stale, other], 200.0)

    assert reports == []
    assert [problem.split(": ", 2)[2] for problem in problems] == [
        "report predates this run; rerun T1 for a fresh report",
        "report is for tier t2, not t1; pass only T1 run reports",
    ]

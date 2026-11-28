from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

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


_CONFORMANCE = Path(__file__).parent / "fixtures" / "proofs" / "adapter-conformance"


def _cases(path: str, source: str) -> list[Any]:
    adapter = _lib("pytest_adapter")
    cases: list[Any] = adapter.static_cases(path, source)
    return cases


def test_parameter_rows_share_one_static_case() -> None:
    source = (_CONFORMANCE / "behaviour-name-present" / "tests" / "test_totals.py").read_text(
        encoding="utf-8"
    )

    cases = _cases("tests/test_totals.py", source)

    assert [case.id for case in cases] == ["tests/test_totals.py::test_total_adds_every_amount"]


def test_class_methods_carry_their_class_in_the_id() -> None:
    source = "class TestTotals:\n    def test_adds_amounts(self):\n        assert 1\n"

    assert [case.id for case in _cases("tests/test_t.py", source)] == [
        "tests/test_t.py::TestTotals::test_adds_amounts"
    ]


def test_renamed_locals_keep_the_same_fingerprint() -> None:
    first = "def test_a():\n    value = load(1)\n    assert value == 2\n"
    second = "def test_b():\n    result = load(1)\n    assert result == 2\n"

    left, right = _cases("tests/test_x.py", first + "\n\n" + second)

    assert left.fingerprint == right.fingerprint


def test_different_inputs_give_different_fingerprints() -> None:
    first = "def test_a():\n    value = load(1)\n    assert value == 2\n"
    second = "def test_b():\n    value = load(3)\n    assert value == 2\n"

    left, right = _cases("tests/test_x.py", first + "\n\n" + second)

    assert left.fingerprint != right.fingerprint


def test_a_new_duplicate_names_both_tests_and_the_fix() -> None:
    growth = _script("check-test-growth")
    source = (
        "def test_adds_two_amounts():\n    assert total([1, 2]) == 3\n\n\n"
        "def test_sums_a_pair():\n    assert total([1, 2]) == 3\n"
    )
    existing, new = _cases("tests/test_x.py", source)

    problems = growth._duplicates([new], [existing, new])

    assert problems == [
        "tests/test_x.py:5: TEST.duplicate-path: tests/test_x.py::test_sums_a_pair duplicates "
        "tests/test_x.py::test_adds_two_amounts (same normalised calls and assertions); "
        "merge the two tests or remove one"
    ]


def test_two_new_duplicates_are_reported_once() -> None:
    growth = _script("check-test-growth")
    source = (
        "def test_adds_two_amounts():\n    assert total([1, 2]) == 3\n\n\n"
        "def test_sums_a_pair():\n    assert total([1, 2]) == 3\n"
    )
    cases = _cases("tests/test_x.py", source)

    assert len(growth._duplicates(cases, cases)) == 1


def test_bun_titles_split_into_behaviour_words() -> None:
    adapter = _lib("bun_adapter")

    assert adapter.behaviour_words("rejects a foreign host") == ["rejects", "a", "foreign", "host"]

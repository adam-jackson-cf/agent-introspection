from __future__ import annotations

from tests.support.script_loader import load_script

checker = load_script("check-test-tiers")
EVERYTHING = [
    "tests/test_a.py::test_one",
    "tests/test_a.py::test_two",
    "tests/test_b.py::test_three",
]


def test_disjoint_selectors_that_cover_every_file_are_clean() -> None:
    selected = {"t1": EVERYTHING[:2], "t2": EVERYTHING[2:]}
    assert checker.tier_problems(selected, EVERYTHING) == []


def test_an_unselected_test_is_reported() -> None:
    selected = {"t1": EVERYTHING[:2], "t2": ["tests/test_c.py::test_four"]}
    problems = checker.tier_problems(selected, [*EVERYTHING, *selected["t2"]])
    assert problems == [
        "tests/test_b.py: TEST.tier-selectors: tests/test_b.py::test_three is selected by no "
        "tier; tag it so exactly one selector matches"
    ]


def test_an_overlapping_selection_is_reported() -> None:
    problems = checker.tier_problems({"t1": EVERYTHING, "t2": EVERYTHING[2:]}, EVERYTHING)
    assert any("selected by t1, t2" in problem for problem in problems)


def test_a_file_with_two_tiers_is_reported() -> None:
    problems = checker.tier_problems({"t1": EVERYTHING[:1], "t2": EVERYTHING[1:]}, EVERYTHING)
    assert problems == [
        "tests/test_a.py: TEST.tier-selectors: holds tests of tiers t1, t2; move the tests of "
        "one tier into their own file so each file has one tier"
    ]


def test_an_empty_tier_is_reported() -> None:
    problems = checker.tier_problems({"t1": EVERYTHING, "t2": []}, EVERYTHING)
    assert problems == [
        "TEST.tier-selectors: tier t2 selects no tests; tag tests for it or drop the tier"
    ]


def test_every_tier_must_be_named_with_its_selector() -> None:
    tiers, paths = checker.parse_tiers(["--tier", "t1=not boundary", "--tier", "t2=boundary", "x"])
    assert (tiers, paths) == ({"t1": "not boundary", "t2": "boundary"}, ["x"])


def test_a_collected_id_with_spaces_in_its_parameters_is_kept() -> None:
    output = (
        "tests/test_a.py::test_one[import subprocess as sp]\n"
        "tests/test_a.py::test_two\n"
        "\n"
        "2 tests collected in 0.01s\n"
    )
    assert checker.listed_ids(output) == [
        "tests/test_a.py::test_one[import subprocess as sp]",
        "tests/test_a.py::test_two",
    ]

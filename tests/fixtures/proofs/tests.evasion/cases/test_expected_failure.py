import pytest


@pytest.mark.xfail(reason="known defect")
def test_a_case_marked_as_an_expected_failure() -> None:
    assert sorted([3, 1, 2]) == [3, 2, 1]

import pytest


@pytest.mark.flaky(reruns=3)
def test_a_case_that_reruns_until_green() -> None:
    assert sorted([3, 1, 2]) == [1, 2, 3]

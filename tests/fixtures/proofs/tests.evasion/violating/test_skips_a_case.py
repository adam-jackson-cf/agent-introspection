import pytest


@pytest.mark.skip(reason="waiting on vendor fix")
def test_a_case_that_is_skipped() -> None:
    assert sorted([3, 1, 2]) == [1, 2, 3]

import pytest


@pytest.mark.parametrize("left,right", [(1, 2)])
def test_pair(left: int, right: int) -> None:
    assert left < right

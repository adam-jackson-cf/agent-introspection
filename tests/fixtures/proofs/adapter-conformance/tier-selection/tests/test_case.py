import pytest


def test_fast_total():
    assert sum([1, 2]) == 3


@pytest.mark.boundary
def test_slow_seam_total():
    assert sum([1, 2]) == 3

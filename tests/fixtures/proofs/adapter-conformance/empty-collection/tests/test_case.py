import pytest


@pytest.mark.boundary
def test_slow_seam_total():
    assert sum([1, 2]) == 3

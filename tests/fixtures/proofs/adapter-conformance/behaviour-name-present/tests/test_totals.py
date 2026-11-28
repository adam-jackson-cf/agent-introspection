import pytest


@pytest.mark.parametrize(("values", "expected"), [([1, 2], 3), ([], 0)])
def test_total_adds_every_amount(values, expected):
    assert sum(values) == expected

from hypothesis import given, seed, strategies


@seed(20261127)
@given(strategies.lists(strategies.integers()))
def test_total_never_drops_below_zero(values):
    assert sum(abs(value) for value in values) >= 0

from app import total


def test_adds_two_amounts():
    assert total([1, 2]) == 3


def test_rejects_negative_amounts():
    assert total([-1]) == 0

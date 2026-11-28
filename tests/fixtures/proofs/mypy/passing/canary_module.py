from tests.fixtures.proofs.mypy.passing.imported import VALUE


def double(value: int) -> int:
    return value * 2


RESULT: int = double(VALUE)

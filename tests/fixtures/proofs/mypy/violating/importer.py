from tests.fixtures.proofs.mypy.violating.broken import VALUE


def double(value: int) -> int:
    return value * 2


RESULT: int = double(VALUE)

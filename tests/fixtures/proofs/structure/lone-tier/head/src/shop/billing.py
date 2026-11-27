from shop.ledger import record
from shop.money import cents


def bill(value: int) -> int:
    return record(cents(value))

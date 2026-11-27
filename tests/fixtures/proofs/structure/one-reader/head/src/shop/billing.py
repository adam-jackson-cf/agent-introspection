from shop.money import cents
from shop.rounding import round_up


def bill(value: int) -> int:
    return round_up(cents(value))

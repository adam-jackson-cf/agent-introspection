from shop.money import cents
from shop.tax import levy


def price(value: int) -> int:
    return cents(value) - levy(value)

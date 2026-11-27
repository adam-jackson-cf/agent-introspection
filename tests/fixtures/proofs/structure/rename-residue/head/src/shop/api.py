from shop.billing import bill
from shop.catalog import price


def serve(value: int) -> int:
    return bill(value) - price(value)

from shop.billing import bill
from shop.catalog import price
from shop.invoice import invoice


def serve(value: int) -> int:
    return bill(value) - price(value) - invoice(value)

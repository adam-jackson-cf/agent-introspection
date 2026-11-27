from shop.billing import bill


def invoice(value: int) -> int:
    return bill(value)

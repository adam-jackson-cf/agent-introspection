def read(obj: object) -> object:
    return getattr(obj, "name")

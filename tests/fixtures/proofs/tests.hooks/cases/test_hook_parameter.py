def endpoint(*, for_testing: bool = False) -> str:
    return "http://127.0.0.1:1" if for_testing else "http://127.0.0.1:4318"

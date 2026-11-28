import os


def endpoint() -> str:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return "http://127.0.0.1:1"
    return "http://127.0.0.1:4318"

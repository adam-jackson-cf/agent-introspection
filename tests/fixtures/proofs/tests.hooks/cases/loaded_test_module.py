import sys


def endpoint() -> str:
    return "http://127.0.0.1:1" if "pytest" in sys.modules else "http://127.0.0.1:4318"

import json


def parse(text: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"not JSON: {error.msg}") from error

import json


def parse(text: str) -> object:
    try:
        return json.loads(text)
    except Exception as error:
        return str(error)

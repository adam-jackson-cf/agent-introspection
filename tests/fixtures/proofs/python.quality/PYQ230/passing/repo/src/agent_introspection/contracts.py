from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

type Handler = Callable[[str], str]


@dataclass
class Contract:
    handler: Handler

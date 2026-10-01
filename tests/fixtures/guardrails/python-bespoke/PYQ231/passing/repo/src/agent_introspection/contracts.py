from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

@dataclass
class Contract:
    payload: dict[str, str]

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol, overload


class Reader(Protocol):
    def read(self) -> str: ...


class Base(ABC):
    @abstractmethod
    def run(self) -> None:
        raise NotImplementedError


@overload
def parse(value: int) -> int: ...
@overload
def parse(value: str) -> str: ...
def parse(value: int | str) -> int | str:
    return value

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    line: int
    rule: str
    check: str
    problem: str
    fix: str

    def render(self) -> str:
        location = f"{self.path}:{self.line}" if self.line else self.path
        return f"{location}: {self.rule}: {self.problem}; {self.fix}"

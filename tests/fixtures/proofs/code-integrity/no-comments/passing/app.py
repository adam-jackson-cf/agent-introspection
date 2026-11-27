#!/usr/bin/env python3
from __future__ import annotations

import os  # noqa: F401

VALUE: int = "text"  # type: ignore[assignment]
GRID = [
    1, 2,
]  # fmt: skip
URL = "https://example.com/#anchor"
SHAPE = "a # not a comment"


def double(value: int) -> int:
    return value * 2

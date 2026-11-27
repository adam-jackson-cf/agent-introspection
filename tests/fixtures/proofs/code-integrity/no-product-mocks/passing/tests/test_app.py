from __future__ import annotations

from unittest import mock


def test_double() -> None:
    assert mock.sentinel.value is mock.sentinel.value

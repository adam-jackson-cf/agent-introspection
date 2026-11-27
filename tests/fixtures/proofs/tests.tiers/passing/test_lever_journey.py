from __future__ import annotations

import pytest

pytestmark = [pytest.mark.boundary, pytest.mark.deferred]


def test_the_journey_tier_holds_a_journey() -> None:
    assert sum([1, 2]) == 3

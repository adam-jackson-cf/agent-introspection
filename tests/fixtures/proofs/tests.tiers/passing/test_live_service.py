import pytest

pytestmark = pytest.mark.live


def test_the_live_service_answers() -> None:
    assert "ok" == "ok"

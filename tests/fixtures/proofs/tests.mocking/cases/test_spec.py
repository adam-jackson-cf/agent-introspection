from unittest.mock import Mock

from agent_introspection.config import AppConfig


def test_builds_a_mock_specced_on_an_owned_class() -> None:
    assert Mock(spec=AppConfig) is not None

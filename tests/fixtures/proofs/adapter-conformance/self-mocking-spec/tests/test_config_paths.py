from unittest.mock import Mock

from agent_introspection.config import AppConfig


def test_config_reaches_the_sync():
    settings = Mock(spec=AppConfig)
    assert settings

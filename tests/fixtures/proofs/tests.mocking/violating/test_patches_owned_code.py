import pytest

from agent_introspection import spool


def test_a_test_that_patches_owned_logic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spool, "sync", lambda *args, **kwargs: {})

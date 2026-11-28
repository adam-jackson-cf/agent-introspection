import pytest

from agent_introspection import findings


def test_patches_an_imported_module_attribute(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(findings, "refresh", lambda *args: {})

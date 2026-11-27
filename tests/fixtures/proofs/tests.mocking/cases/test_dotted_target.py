import pytest


def test_patches_by_dotted_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent_introspection.findings.refresh", lambda *args: {})

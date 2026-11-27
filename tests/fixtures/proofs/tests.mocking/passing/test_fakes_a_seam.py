import pytest

from agent_introspection import spool


def test_a_test_fakes_only_the_declared_host_name_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spool, "host_name", lambda: "canary")
    assert spool.host_name() == "canary"

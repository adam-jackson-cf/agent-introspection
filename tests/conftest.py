import sqlite3
from collections.abc import Callable, Iterator
from contextlib import ExitStack, closing
from pathlib import Path

import pytest

from agent_introspection import hooks
from agent_introspection.workflow import connect_workflow
from tests.support.otlp_collector import Collector

type OpenWorkflow = Callable[..., sqlite3.Connection]


@pytest.fixture
def open_workflow() -> Iterator[OpenWorkflow]:
    """Open workflow stores that the test closes when it ends."""
    with ExitStack() as stack:

        def open_store(path: Path | str = ":memory:", **options: int) -> sqlite3.Connection:
            return stack.enter_context(closing(connect_workflow(path, **options)))

        yield open_store


@pytest.fixture
def collector() -> Iterator[Collector]:
    """A local OTLP/HTTP logs collector, shut down when the test ends."""
    server = Collector()
    yield server
    server.close()


@pytest.fixture
def inbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated record spool and hook log, with no OTEL endpoint in the environment."""
    directory = tmp_path / "data" / "hook-inbox"
    monkeypatch.setattr(hooks, "INBOX", directory)
    monkeypatch.setattr(hooks, "LOG", tmp_path / "data" / "hooks.log")
    for name in (
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
        "OTEL_EXPORTER_OTLP_PROTOCOL",
        "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL",
    ):
        monkeypatch.delenv(name, raising=False)
    return directory

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import ExitStack, closing
from pathlib import Path

import pytest

from agent_introspection.workflow import connect_workflow

type OpenWorkflow = Callable[..., sqlite3.Connection]


@pytest.fixture
def open_workflow() -> Iterator[OpenWorkflow]:
    """Open workflow stores that the test closes when it ends."""
    with ExitStack() as stack:

        def open_store(path: Path | str = ":memory:", **options: int) -> sqlite3.Connection:
            return stack.enter_context(closing(connect_workflow(path, **options)))

        yield open_store

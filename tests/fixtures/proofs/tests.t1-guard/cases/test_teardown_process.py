import subprocess
from collections.abc import Iterator

import pytest


@pytest.fixture
def leaves_a_process() -> Iterator[None]:
    yield
    subprocess.run(["true"], check=True, timeout=5)


def test_a_t1_fixture_that_starts_a_process_in_teardown(leaves_a_process: None) -> None:
    assert leaves_a_process is None

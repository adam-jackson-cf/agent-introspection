import subprocess

import pytest


def test_the_sum_is_computed_in_memory() -> None:
    assert 1 + 2 == 3


@pytest.mark.boundary
def test_a_real_process_exits_cleanly() -> None:
    assert subprocess.run(["true"], check=False, timeout=5).returncode == 0

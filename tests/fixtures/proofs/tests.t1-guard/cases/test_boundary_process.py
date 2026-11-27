import subprocess

import pytest

pytestmark = pytest.mark.boundary


def test_a_boundary_test_may_start_a_process() -> None:
    subprocess.run(["true"], check=True, timeout=5)

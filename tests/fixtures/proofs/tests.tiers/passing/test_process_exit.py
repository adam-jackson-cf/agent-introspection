import subprocess

import pytest

pytestmark = pytest.mark.boundary


def test_a_real_process_exits_cleanly() -> None:
    assert subprocess.run(["true"], check=False, timeout=5).returncode == 0

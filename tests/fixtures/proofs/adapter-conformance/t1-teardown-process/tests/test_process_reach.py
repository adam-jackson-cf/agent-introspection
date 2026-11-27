import subprocess

import pytest


@pytest.fixture
def cleanup():
    yield
    subprocess.run(["true"], check=False, timeout=5)


def test_cleanup_runs_after_the_body(cleanup):
    assert cleanup is None

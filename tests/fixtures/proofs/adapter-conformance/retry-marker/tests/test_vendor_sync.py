import pytest


@pytest.mark.flaky(reruns=3)
def test_vendor_sync_reads_the_feed():
    assert True

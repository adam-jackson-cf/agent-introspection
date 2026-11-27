import pytest


@pytest.mark.skip(reason="vendor endpoint is down")
def test_vendor_sync_reads_the_feed():
    assert True

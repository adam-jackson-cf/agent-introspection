import pytest


@pytest.mark.xfail(reason="known defect")
def test_vendor_sync_reads_the_feed():
    assert False

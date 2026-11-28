import subprocess


def test_cache_page_loads_through_the_lever():
    completed = subprocess.run(["bash", "scripts/lever.sh", "lever"], check=False, timeout=600)
    assert completed.returncode == 0

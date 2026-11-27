import subprocess


def test_a_t1_test_that_starts_a_process() -> None:
    subprocess.run(["true"], check=True, timeout=5)

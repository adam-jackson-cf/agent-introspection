import subprocess


def test_the_process_call_has_no_bound() -> None:
    subprocess.run(["true"], check=False, timeout=0)

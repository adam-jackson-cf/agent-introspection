import subprocess


def test_the_process_call_is_bounded() -> None:
    subprocess.run(["true"], check=False, timeout=5)

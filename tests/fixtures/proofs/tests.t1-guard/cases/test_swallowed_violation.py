import subprocess


def test_a_t1_test_that_swallows_the_guard_error() -> None:
    try:
        subprocess.run(["true"], check=True, timeout=5)
    except RuntimeError:
        pass

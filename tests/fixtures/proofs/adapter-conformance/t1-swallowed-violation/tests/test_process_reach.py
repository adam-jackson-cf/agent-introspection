import subprocess


def test_true_command_is_optional():
    try:
        subprocess.run(["true"], check=False, timeout=5)
    except Exception:
        pass

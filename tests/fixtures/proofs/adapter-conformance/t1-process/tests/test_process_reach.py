import subprocess


def test_true_command_exits_zero():
    assert subprocess.run(["true"], check=False, timeout=5).returncode == 0

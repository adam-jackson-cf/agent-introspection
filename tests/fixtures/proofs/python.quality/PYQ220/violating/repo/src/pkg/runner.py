import subprocess


def helper() -> int:
    return subprocess.run(["true"], check=False).returncode

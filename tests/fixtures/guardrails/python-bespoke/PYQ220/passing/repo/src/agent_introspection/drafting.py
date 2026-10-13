import subprocess


def run_codex() -> int:
    return subprocess.run(["true"], check=False).returncode

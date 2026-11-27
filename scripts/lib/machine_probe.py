from __future__ import annotations

import platform
import re
import sys
from pathlib import Path

from lib.command_runner import run_command

PROBE_TIMEOUT_SECONDS = 20


def _output(argv: list[str]) -> str:
    completed = run_command(argv, Path.cwd(), PROBE_TIMEOUT_SECONDS)
    if completed.returncode != 0:
        raise RuntimeError(f"{' '.join(argv)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def cpu_model() -> str:
    if sys.platform == "darwin":
        return _output(["sysctl", "-n", "machdep.cpu.brand_string"])
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in cpuinfo.read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    return platform.processor() or "unknown-cpu"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", text.lower()).strip("-")


def machine_id() -> str:
    python = f"python{sys.version_info.major}.{sys.version_info.minor}"
    bun = f"bun{_output(['bun', '--version'])}"
    parts = (sys.platform, platform.machine(), cpu_model(), python, bun)
    return "-".join(_slug(part) for part in parts)

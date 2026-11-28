from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path


def isolated_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment.update(extra or {})
    return environment


def run_command(
    argv: Sequence[str],
    cwd: Path,
    timeout_seconds: float,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=cwd,
        env=dict(environment) if environment is not None else isolated_environment(),
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )

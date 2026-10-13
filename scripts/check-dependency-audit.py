#!/usr/bin/env python3
"""Fail on known-vulnerable locked dependencies (SECURITY.dependency-audit).

Python: `uv export --locked` audited by pip-audit. Bun: `bun audit` in dashboard/.
Pass a requirements file to audit only that file (used by the canary).
Needs network access to the vulnerability databases.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

_TIMEOUT_SECONDS = 100
_PIP_AUDIT = ["uvx", "pip-audit==2.10.1", "--strict", "--disable-pip", "--no-deps", "-r"]


def _run(argv: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=_TIMEOUT_SECONDS,
    )


def audit_python(requirements: Path) -> int:
    """Audit a pinned requirements file with pip-audit."""
    result = _run([*_PIP_AUDIT, str(requirements)])
    if result.returncode != 0:
        print(result.stdout + result.stderr, file=sys.stderr)
        print("SECURITY.dependency-audit python dependencies have findings", file=sys.stderr)
    return result.returncode


def audit_bun(directory: Path) -> int:
    """Audit the Bun lockfile in directory."""
    result = _run(["bun", "audit"], cwd=directory)
    if result.returncode != 0:
        print(result.stdout + result.stderr, file=sys.stderr)
        print("SECURITY.dependency-audit bun dependencies have findings", file=sys.stderr)
    return result.returncode


def main(argv: list[str]) -> int:
    """Audit an explicit requirements file, or the locked Python and Bun dependencies."""
    if argv:
        return audit_python(Path(argv[0]))
    with tempfile.TemporaryDirectory() as directory:
        requirements = Path(directory) / "requirements.txt"
        exported = _run([
            "uv",
            "export",
            "--locked",
            "--no-emit-project",
            "--format",
            "requirements-txt",
            "--output-file",
            str(requirements),
        ])
        if exported.returncode != 0:
            print(exported.stderr, file=sys.stderr)
            print(
                "SECURITY.dependency-audit could not export locked Python dependencies",
                file=sys.stderr,
            )
            return 1
        python_status = audit_python(requirements)
    bun_status = audit_bun(Path("dashboard"))
    return 1 if python_status or bun_status else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.boundary

SCRIPTS = Path(__file__).parents[1] / "scripts"
LIBRARIES = ("harness_render.py", "filesystem.py")


def _render(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "render-measures.py"), *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_render_measures_writes_a_missing_harness_module_and_then_reports_it_current(
    tmp_path: Path,
) -> None:
    (tmp_path / "scripts" / "lib").mkdir(parents=True)
    shutil.copy(SCRIPTS / "render-measures.py", tmp_path / "scripts")
    for name in LIBRARIES:
        shutil.copy(SCRIPTS / "lib" / name, tmp_path / "scripts" / "lib")
    (tmp_path / "dashboard" / "src").mkdir(parents=True)

    stale = _render(tmp_path, "--check")
    assert stale.returncode == 1
    assert "out of date" in stale.stdout

    assert _render(tmp_path).returncode == 0
    assert (tmp_path / "dashboard" / "src" / "harnesses.generated.ts").read_text().strip()

    current = _render(tmp_path, "--check")
    assert current.returncode == 0, current.stdout + current.stderr

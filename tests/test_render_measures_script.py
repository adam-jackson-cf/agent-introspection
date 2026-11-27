from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.boundary

SCRIPTS = Path(__file__).parents[1] / "scripts"
RENDERERS = ("measures_render.py", "harness_render.py", "capabilities_render.py")


def _render(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "render-measures.py"), *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_render_measures_writes_missing_outputs_and_then_reports_them_current(
    tmp_path: Path,
) -> None:
    (tmp_path / "scripts" / "lib").mkdir(parents=True)
    shutil.copy(SCRIPTS / "render-measures.py", tmp_path / "scripts")
    for name in RENDERERS:
        shutil.copy(SCRIPTS / "lib" / name, tmp_path / "scripts" / "lib")
    (tmp_path / "docs" / "references").mkdir(parents=True)
    (tmp_path / "dashboard" / "src").mkdir(parents=True)

    stale = _render(tmp_path, "--check")
    assert stale.returncode == 1
    assert "out of date" in stale.stdout

    assert _render(tmp_path).returncode == 0
    for path in (
        tmp_path / "docs" / "references" / "signal-catalog.md",
        tmp_path / "docs" / "references" / "data-gaps.md",
        tmp_path / "docs" / "references" / "producer-capabilities.md",
        tmp_path / "dashboard" / "src" / "harnesses.generated.ts",
    ):
        assert path.read_text().strip()
    assert (
        "BEGIN GENERATED FROM signal_support.toml"
        in (tmp_path / "docs" / "references" / "data-gaps.md").read_text()
    )

    current = _render(tmp_path, "--check")
    assert current.returncode == 0, current.stdout + current.stderr

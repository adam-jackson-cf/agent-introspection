from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agent_introspection import facts

pytestmark = pytest.mark.boundary

PACKAGE = Path(facts.__file__).parent


def _run_registry_probe(package_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", "from agent_introspection import facts; facts.registry_harnesses()"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env={**os.environ, "PYTHONPATH": str(package_root), "PYTHONDONTWRITEBYTECODE": "1"},
    )


def test_a_registry_without_harnesses_is_refused(tmp_path: Path) -> None:
    copy = tmp_path / "agent_introspection"
    shutil.copytree(PACKAGE, copy, ignore=shutil.ignore_patterns("__pycache__"))
    (copy / "facts_sql" / facts.REGISTRY_FILE).write_text("[harnesses]\n", encoding="utf-8")
    completed = _run_registry_probe(tmp_path)
    assert completed.returncode == 1
    assert "RegistryError: registry needs [harnesses]" in completed.stderr

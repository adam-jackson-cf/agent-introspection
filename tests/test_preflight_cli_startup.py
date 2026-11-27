from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = pytest.mark.boundary


_BROKEN_REGISTRY_IMPORT = """
from agent_introspection import facts
original = facts._sql
facts._sql = lambda name: "[[broken\\n" if name == facts.REGISTRY_FILE else original(name)
import agent_introspection.cli
"""


def _run_python(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_a_registry_that_does_not_parse_never_stops_the_cli_from_starting() -> None:
    # `hook`, `spool sync`, and `doctor` must start while the registry is mid-edit;
    # only the commands that read it fail.
    result = _run_python(_BROKEN_REGISTRY_IMPORT)

    assert result.returncode == 0, result.stderr

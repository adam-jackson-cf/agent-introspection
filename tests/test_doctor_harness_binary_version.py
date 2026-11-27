from __future__ import annotations

import sys

import pytest

from agent_introspection import doctor_harness

pytestmark = pytest.mark.boundary


def test_installed_version_runs_the_binary_and_reads_its_version() -> None:
    version = doctor_harness.installed_version([sys.executable, "--version"])

    assert version == ".".join(map(str, sys.version_info[:3]))

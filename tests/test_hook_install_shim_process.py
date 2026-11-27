from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from agent_introspection import doctor_harness, hook_install
from agent_introspection.hook_install import HookOptions

pytestmark = pytest.mark.boundary


def test_the_shim_exports_the_pointed_config_unless_one_is_set(tmp_path: Path) -> None:
    hook_install.install_runtime(
        HookOptions(home=tmp_path, config_path=tmp_path / "c.toml"), "claude-code"
    )
    shim = tmp_path / doctor_harness.MANAGED_RUNTIME / doctor_harness.SHIM
    seen = tmp_path / "seen"
    fake = tmp_path / "bin/agent-introspection"
    fake.parent.mkdir()
    fake.write_text(f'#!/bin/sh\nprintf %s "$AGENT_INTROSPECTION_CONFIG" > "{seen}"\n')
    fake.chmod(0o755)

    for preset, expected in ((None, str((tmp_path / "c.toml").resolve())), ("/x.toml", "/x.toml")):
        seen.unlink(missing_ok=True)
        environment = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "AGENT_INTROSPECTION_BIN": str(fake),
        }
        if preset is not None:
            environment["AGENT_INTROSPECTION_CONFIG"] = preset
        subprocess.run(
            ["/bin/sh", str(shim), "omp", "tool_call"],
            input="{}",
            env=environment,
            text=True,
            timeout=10,
            check=True,
        )
        for _ in range(100):
            if seen.exists():
                break
            time.sleep(0.05)
        assert seen.read_text() == expected

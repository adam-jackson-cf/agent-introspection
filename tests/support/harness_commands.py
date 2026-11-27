from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_introspection.cli import main


def config_file(tmp_path: Path, text: str = "") -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


def run_harness(
    capsys: pytest.CaptureFixture[str], config: Path, *argv: str
) -> tuple[dict[str, Any], str]:
    assert main(["--config", str(config), "harness", *argv]) == 0
    out = capsys.readouterr().out
    return json.loads(out), out


def fake_binary(directory: Path, name: str, output: str) -> Path:
    binary = directory / name
    binary.write_text(
        f'#!/bin/sh\necho "$@" > "{directory / (name + ".ran")}"\nprintf "{output}"\nexit 0\n'
    )
    binary.chmod(0o755)
    return binary

from __future__ import annotations

import time
from pathlib import Path

import pytest

from agent_introspection import doctor_harness
from tests.support.harness_commands import config_file, fake_binary, run_harness

pytestmark = pytest.mark.boundary


def test_harness_list_reports_status_enablement_the_binary_and_its_version(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binaries = tmp_path / "bin"
    binaries.mkdir()
    claude = fake_binary(binaries, "claude", "2.1.285 (Claude Code)\\n")
    fake_binary(binaries, "omp", "omp/0.0.1\\n")
    fake_binary(binaries, "opencode", "")
    monkeypatch.setenv("PATH", str(binaries))
    config = config_file(tmp_path, '[harnesses]\nenabled = ["claude-code", "codex_exec"]\n')

    result, _ = run_harness(capsys, config, "list")

    listed = {item["key"]: item for item in result["harnesses"]}
    assert result["enabled"] == ["codex_exec", "claude-code"]
    assert list(listed) == ["claude-code", "codex", "omp", "opencode"]
    assert (listed["claude-code"]["binary_found"], listed["claude-code"]["binary_path"]) == (
        True,
        str(claude),
    )
    assert listed["codex"]["binary_found"] is False
    assert (listed["codex"]["enabled"], listed["codex"]["enabled_keys"]) == (True, ["codex_exec"])
    assert listed["omp"]["enabled"] is False
    assert (listed["opencode"]["status"], listed["opencode"]["enabled"]) == ("candidate", False)
    versions = {
        key: (item["installed_version"], item["version_verified"], item.get("version_note"))
        for key, item in listed.items()
    }
    assert versions == {
        "claude-code": ("2.1.285", True, None),
        "codex": (None, None, None),
        "omp": ("0.0.1", False, None),
        "opencode": (None, None, "`opencode --version` printed no version"),
    }
    assert (binaries / "claude.ran").read_text() == "--version\n"


def test_each_version_probe_is_bounded_and_reads_no_stdin(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binaries = tmp_path / "bin"
    binaries.mkdir()
    hangs = binaries / "claude"
    hangs.write_text("#!/bin/sh\nexec /bin/sleep 30\n")
    reads = binaries / "codex"
    reads.write_text('#!/bin/sh\nread -r line\nprintf "1.0.%s\\n" "$?"\n')
    for binary in (hangs, reads):
        binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(binaries))
    started = time.monotonic()

    result, _ = run_harness(capsys, config_file(tmp_path), "list")

    elapsed = time.monotonic() - started
    listed = {item["key"]: item for item in result["harnesses"]}
    assert listed["claude-code"]["version_note"] == "`claude --version` failed (TimeoutExpired)"
    assert elapsed < doctor_harness.VERSION_TIMEOUT_SECONDS + 5
    assert listed["codex"]["installed_version"] == "1.0.1"

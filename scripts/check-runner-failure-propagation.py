#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from lib import filesystem
from lib.command_runner import isolated_environment

_STUBBED_TOOLS = ("uv", "bun", "bunx", "npx", "pre-commit")
_STEP_TIMEOUT_SECONDS = 20
_IDENTITY = ["-c", "user.name=scratch", "-c", "user.email=scratch@localhost"]
_STUB = """#!/bin/sh
count=$(($(cat "$STUB_STATE/count" 2>/dev/null || echo 0) + 1))
echo "$count" > "$STUB_STATE/count"
echo "$count $(basename "$0") $*" >> "$STUB_STATE/calls"
if [ "$count" = "${STUB_FAIL_AT:-0}" ]; then
  echo "stub failure at call $count: $(basename "$0") $*" >&2
  exit 3
fi
case "$*" in *list-python-scope*) printf 'scripts/stub.py\\0' ;; esac
exit 0
"""


def _run(
    argv: list[str], cwd: Path, env: dict[str, str] | None, *, check: bool
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        env=isolated_environment() if env is None else env,
        check=check,
        capture_output=True,
        text=True,
        timeout=_STEP_TIMEOUT_SECONDS,
    )


def _scratch_repository(runner: Path, directory: Path) -> Path:
    root = runner.resolve().parent.parent
    repository = directory / "repo"
    filesystem.copy_tree(
        root / runner.resolve().parent.name, repository / runner.resolve().parent.name
    )
    for optional in (".pre-commit-config.yaml", ".github"):
        source = root / optional
        if filesystem.is_dir(source):
            filesystem.copy_tree(source, repository / optional)
        elif filesystem.is_file(source):
            filesystem.copy_file(source, repository / optional)
    filesystem.write_text(repository / "oxlint.config.ts", "export default {};\n")
    filesystem.write_text(repository / "README.md", "# Scratch\n")
    filesystem.write_text(repository / "scripts" / "stub.py", "")
    for argv in (
        ["git", "init", "-q"],
        ["git", "add", "-A"],
        ["git", *_IDENTITY, "commit", "-qm", "scratch"],
    ):
        _run(argv, repository, None, check=True)
    bin_dir = directory / "bin"
    filesystem.mkdir(bin_dir)
    for tool in _STUBBED_TOOLS:
        stub = bin_dir / tool
        filesystem.write_text(stub, _STUB)
        filesystem.make_executable(stub)
    return repository


def _run_runner(runner: Path, repository: Path, directory: Path, mode: str, fail_at: int) -> int:
    state = directory / "state"
    filesystem.mkdir(state, exist_ok=True)
    for leftover in filesystem.iterdir(state):
        filesystem.unlink(leftover)
    scratch = isolated_environment()
    env = {
        **scratch,
        "CI": "true",
        "PATH": f"{directory / 'bin'}{os.pathsep}{scratch['PATH']}",
        "STUB_STATE": str(state),
        "STUB_FAIL_AT": str(fail_at),
    }
    completed = _run(
        ["bash", str(repository / runner.resolve().parent.name / runner.name), mode],
        repository,
        env,
        check=False,
    )
    return completed.returncode


def _check_mode(runner: Path, repository: Path, directory: Path, mode: str) -> list[str]:
    if _run_runner(runner, repository, directory, mode, 0) != 0:
        return [
            f"{runner}: TEST.failure-propagation: mode {mode} fails with passing tools; "
            "make the control run pass"
        ]
    count = directory / "state" / "count"
    calls = int(filesystem.read_text(count)) if filesystem.exists(count) else 0
    if calls == 0:
        return [
            f"{runner}: TEST.failure-propagation: mode {mode} invoked no stubbed tools; "
            "run the gate's tools through the runner"
        ]
    print(
        f"check-runner-failure-propagation: mode {mode}: {calls} tool calls exercised",
        file=sys.stderr,
    )
    problems: list[str] = []
    for index in range(1, calls + 1):
        if _run_runner(runner, repository, directory, mode, index) == 0:
            call = filesystem.read_text(directory / "state" / "calls").splitlines()[index - 1]
            problems.append(
                f"{runner}: TEST.failure-propagation: mode {mode} passed although tool call "
                f"#{index} failed ({call}); propagate that call's nonzero exit"
            )
    return problems


def check_runner(runner: Path, modes: list[str]) -> list[str]:
    with tempfile.TemporaryDirectory() as raw_directory:
        directory = Path(raw_directory)
        repository = _scratch_repository(runner, directory)
        return [
            problem
            for mode in modes
            for problem in _check_mode(runner, repository, directory, mode)
        ]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: check-runner-failure-propagation.py RUNNER MODE [MODE ...]", file=sys.stderr)
        return 2
    problems = check_runner(Path(argv[0]), argv[1:])
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"check-runner-failure-propagation: {len(problems)} violations", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""Fail when a failing runner step can still yield a passing gate (TEST.failure-propagation).

Runs the runner in a scratch repository whose `uv`, `bun`, `bunx`, `npx` and `pre-commit`
commands are stubs. A control run must pass; then each stubbed tool invocation
is made to fail in turn, and the runner must exit nonzero every time.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

_STUBBED_TOOLS = ("uv", "bun", "bunx", "npx", "pre-commit")
_STEP_TIMEOUT_SECONDS = 20
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


def _scratch_env() -> dict[str, str]:
    """Return the environment without Git hook variables such as ``GIT_INDEX_FILE``.

    Git exports them to hooks; inheriting them would make scratch-repository commands write to
    the caller's real index.
    """
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def _run(
    argv: list[str], cwd: Path, env: dict[str, str] | None, *, check: bool
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        env=_scratch_env() if env is None else env,
        check=check,
        capture_output=True,
        text=True,
        timeout=_STEP_TIMEOUT_SECONDS,
    )


def _scratch_repository(runner: Path, directory: Path) -> Path:
    """Copy the runner's repository skeleton and put stub tools ahead on PATH."""
    root = runner.resolve().parent.parent
    repository = directory / "repo"
    shutil.copytree(root / runner.resolve().parent.name, repository / runner.resolve().parent.name)
    for optional in (".pre-commit-config.yaml", ".github"):
        source = root / optional
        if source.is_dir():
            shutil.copytree(source, repository / optional)
        elif source.is_file():
            shutil.copy(source, repository / optional)
    (repository / "oxlint.config.ts").write_text("export default {};\n", encoding="utf-8")
    (repository / "README.md").write_text("# Scratch\n", encoding="utf-8")
    (repository / "scripts" / "stub.py").write_text("", encoding="utf-8")
    for argv in (["git", "init", "-q"], ["git", "add", "-A"]):
        _run(argv, repository, None, check=True)
    bin_dir = directory / "bin"
    bin_dir.mkdir()
    for tool in _STUBBED_TOOLS:
        stub = bin_dir / tool
        stub.write_text(_STUB, encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return repository


def _run_runner(runner: Path, repository: Path, state: Path, bin_dir: Path, fail_at: int) -> int:
    state.mkdir(exist_ok=True)
    for leftover in state.iterdir():
        leftover.unlink()
    env = {
        **_scratch_env(),
        "CI": "true",
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "STUB_STATE": str(state),
        "STUB_FAIL_AT": str(fail_at),
    }
    completed = _run(
        ["bash", str(repository / runner.resolve().parent.name / runner.name)],
        repository,
        env,
        check=False,
    )
    return completed.returncode


def check_runner(runner: Path) -> list[str]:
    """Return failure-propagation violations for one runner script."""
    with tempfile.TemporaryDirectory() as raw_directory:
        directory = Path(raw_directory)
        repository = _scratch_repository(runner, directory)
        state, bin_dir = directory / "state", directory / "bin"
        if _run_runner(runner, repository, state, bin_dir, 0) != 0:
            return [f"{runner}: TEST.failure-propagation control run with passing tools failed"]
        calls = (
            int((state / "count").read_text(encoding="utf-8")) if (state / "count").exists() else 0
        )
        if calls == 0:
            return [f"{runner}: TEST.failure-propagation runner invoked no stubbed tools"]
        print(f"check-runner-failure-propagation: {calls} tool calls exercised", file=sys.stderr)
        problems: list[str] = []
        for index in range(1, calls + 1):
            if _run_runner(runner, repository, state, bin_dir, index) == 0:
                call = (state / "calls").read_text(encoding="utf-8").splitlines()[index - 1]
                problems.append(
                    f"{runner}: TEST.failure-propagation runner passed although tool call "
                    f"#{index} failed ({call})"
                )
    return problems


def main(argv: list[str]) -> int:
    """Check the runner named by argv[0]."""
    if len(argv) != 1:
        print("usage: check-runner-failure-propagation.py RUNNER", file=sys.stderr)
        return 2
    problems = check_runner(Path(argv[0]))
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"check-runner-failure-propagation: {len(problems)} violations", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

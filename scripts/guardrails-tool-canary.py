#!/usr/bin/env python3
"""Run a Ruff or mypy guardrail canary against the project's own tool configuration.

A canary fixture is a mini repository: the nearest ancestor directory named ``passing`` or
``violating`` is copied into a scratch root, the project ``pyproject.toml`` is copied over it, and
the tool runs there with the configuration exactly as committed. Scratch roots keep
``tests/**`` per-file ignores, ``tests/fixtures/`` excludes, and the real repository out of the
result.

Marker files in the fixture root:
    ``.canary-apply-fix``  run ``ruff check`` without ``--no-fix`` so ``fix = true`` applies.
    ``gitignore.txt``      becomes ``.gitignore`` in a scratch Git repository.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_MODES = ("ruff-check", "ruff-format", "mypy-file", "mypy-config")
_ROOT_NAMES = frozenset({"passing", "violating"})


def _fixture_root(fixture: Path) -> Path:
    for parent in fixture.resolve().parents:
        if parent.name in _ROOT_NAMES:
            return parent
    raise SystemExit(f"fixture is not inside a 'passing' or 'violating' directory: {fixture}")


def _command(mode: str, root: Path, relative: Path) -> list[str]:
    if mode == "ruff-check":
        fix = [] if (root / ".canary-apply-fix").exists() else ["--no-fix"]
        return [
            sys.executable, "-m", "ruff", "check", "--no-cache", "--config", "pyproject.toml",
            *fix, ".",
        ]  # fmt: skip
    if mode == "ruff-format":
        return [
            sys.executable, "-m", "ruff", "format", "--check", "--no-cache",
            "--config", "pyproject.toml", ".",
        ]  # fmt: skip
    base = [
        sys.executable, "-m", "mypy", "--config-file", "pyproject.toml",
        "--cache-dir", str(root / ".mypy_cache"),
    ]  # fmt: skip
    if mode == "mypy-file":
        return [*base, relative.as_posix()]
    return base


def _seed_mypy_roots(root: Path) -> None:
    """Give ``mypy-config`` the source roots named by the project configuration."""
    for name in ("src", "scripts", "tests"):
        directory = root / name
        if not directory.exists():
            directory.mkdir()
            (directory / f"seed_{name}.py").write_text("SEED: int = 1\n", encoding="utf-8")


def _run(argv: list[str], cwd: Path, *, check: bool) -> subprocess.CompletedProcess[str]:
    # Drop Git hook variables (e.g. GIT_INDEX_FILE) so scratch Git never touches the real index.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run(argv, cwd=cwd, env=env, check=check, capture_output=True, text=True)


def _prepare_ignore_rules(root: Path) -> None:
    """Materialise ``gitignore.txt`` as a real ``.gitignore`` in a scratch Git repository.

    The fixture stores the rules under a neutral name so the outer repository does not apply
    them to the fixture's own files.
    """
    rules = root / "gitignore.txt"
    if rules.exists():
        rules.rename(root / ".gitignore")
        _run(["git", "init", "--quiet"], root, check=True)


def main(argv: list[str] | None = None) -> int:
    """Copy the fixture root, run the requested tool, and mirror its exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=_MODES)
    parser.add_argument("--config", type=Path, required=True, help="project pyproject.toml")
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args(argv)
    fixture_root = _fixture_root(args.fixture)
    relative = args.fixture.resolve().relative_to(fixture_root)
    with tempfile.TemporaryDirectory(prefix="guardrails-canary-") as scratch:
        root = Path(scratch) / "root"
        shutil.copytree(fixture_root, root)
        shutil.copyfile(args.config, root / "pyproject.toml")
        _prepare_ignore_rules(root)
        if args.mode == "mypy-config":
            _seed_mypy_roots(root)
        completed = _run(_command(args.mode, root, relative), root, check=False)
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())

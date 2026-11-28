#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

from lib import filesystem
from lib.command_runner import run_command
from lib.machine_probe import machine_id

MACHINE_TOKEN = "@machine@"
GIT_TIMEOUT_SECONDS = 60
IDENTITY = ("-c", "user.name=scenario", "-c", "user.email=scenario@localhost")


def _git(repo: Path, *args: str) -> str:
    completed = run_command(["git", *IDENTITY, *args], repo, GIT_TIMEOUT_SECONDS)
    if completed.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return str(completed.stdout).strip()


def _place(tree: Path, repo: Path) -> None:
    for entry in filesystem.iterdir(repo):
        if entry.name == ".git":
            continue
        if filesystem.is_dir(entry):
            filesystem.remove_tree(entry)
        else:
            filesystem.unlink(entry)
    filesystem.copy_tree(tree, repo, dirs_exist_ok=True)
    token_files = (
        path
        for path in filesystem.rglob(repo, "*")
        if filesystem.is_file(path) and ".git" not in path.parts
    )
    machine = ""
    for path in token_files:
        text = filesystem.read_text(path)
        if MACHINE_TOKEN in text:
            machine = machine or machine_id()
            filesystem.write_text(path, text.replace(MACHINE_TOKEN, machine))


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def build(fixture: Path, repo: Path) -> str:
    if not filesystem.is_dir(fixture / "head") or not filesystem.is_file(fixture / "argv"):
        raise RuntimeError(f"{fixture} needs a head/ tree and an argv file")
    filesystem.mkdir(repo, parents=True)
    _git(repo, "init", "-q")
    base_tree = fixture / "base"
    if filesystem.is_dir(base_tree):
        _place(base_tree, repo)
    base = _commit(repo, "scenario base")
    _place(fixture / "head", repo)
    _commit(repo, "scenario head")
    return base


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: build-scenario-repo.py FIXTURE DEST", file=sys.stderr)
        return 2
    try:
        print(build(Path(argv[0]), Path(argv[1])))
    except (OSError, RuntimeError) as error:
        print(f"build-scenario-repo: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

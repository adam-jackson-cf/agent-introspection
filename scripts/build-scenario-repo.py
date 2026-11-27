#!/usr/bin/env python3
from __future__ import annotations

import shutil
import sys
from pathlib import Path

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
    for entry in repo.iterdir():
        if entry.name == ".git":
            continue
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    shutil.copytree(tree, repo, dirs_exist_ok=True)
    token_files = (path for path in repo.rglob("*") if path.is_file() and ".git" not in path.parts)
    machine = ""
    for path in token_files:
        text = path.read_text(encoding="utf-8")
        if MACHINE_TOKEN in text:
            machine = machine or machine_id()
            path.write_text(text.replace(MACHINE_TOKEN, machine), encoding="utf-8")


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def build(fixture: Path, repo: Path) -> str:
    if not (fixture / "head").is_dir() or not (fixture / "argv").is_file():
        raise RuntimeError(f"{fixture} needs a head/ tree and an argv file")
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    base_tree = fixture / "base"
    if base_tree.is_dir():
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

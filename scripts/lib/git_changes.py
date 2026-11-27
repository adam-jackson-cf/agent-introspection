from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lib.command_runner import run_command

GIT_TIMEOUT_SECONDS = 60


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class Change:
    status: str
    path: str
    old_path: str | None


def _git(repo: Path, *args: str) -> str:
    completed = run_command(["git", "-C", str(repo), *args], repo, GIT_TIMEOUT_SECONDS)
    if completed.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return str(completed.stdout)


def _split(output: str) -> list[str]:
    return [item for item in output.split("\0") if item]


def resolve(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()


def tracked_files(repo: Path) -> list[str]:
    return _split(_git(repo, "ls-files", "-z"))


def files_at(repo: Path, ref: str) -> list[str]:
    return _split(_git(repo, "ls-tree", "-r", "-z", "--name-only", ref))


def file_at(repo: Path, ref: str, path: str) -> str | None:
    completed = run_command(
        ["git", "-C", str(repo), "show", f"{ref}:{path}"], repo, GIT_TIMEOUT_SECONDS
    )
    return completed.stdout if completed.returncode == 0 else None


def changes_since(repo: Path, base: str) -> list[Change]:
    items = _split(_git(repo, "diff", "--name-status", "-z", "-M", base, "--"))
    changes: list[Change] = []
    index = 0
    while index < len(items):
        status = items[index]
        if status[0] in "RC":
            changes.append(Change(status[0], items[index + 2], items[index + 1]))
            index += 3
        else:
            changes.append(Change(status[0], items[index + 1], None))
            index += 2
    return changes


def added_lines(repo: Path, base: str, paths: list[str]) -> dict[str, list[tuple[int, str]]]:
    if not paths:
        return {}
    diff = _git(repo, "diff", "-U0", "--no-color", "--no-ext-diff", base, "--", *paths)
    found: dict[str, list[tuple[int, str]]] = {}
    current = ""
    line = 0
    for text in diff.splitlines():
        if text.startswith("+++ "):
            current = text[6:] if text.startswith("+++ b/") else ""
        elif text.startswith("@@"):
            line = int(text.split("+", 1)[1].split(" ", 1)[0].split(",", 1)[0])
        elif text.startswith("+") and current:
            found.setdefault(current, []).append((line, text[1:]))
            line += 1
    return found

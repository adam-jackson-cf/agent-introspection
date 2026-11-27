from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from lib import filesystem
from lib.command_runner import isolated_environment

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
_SCENARIO_IDENTITY = [
    "-c",
    "user.name=check-structure",
    "-c",
    "user.email=check-structure@localhost",
]


class GitError(Exception):
    pass


@dataclass(frozen=True)
class Change:
    path: str
    old_path: str | None


def _git(
    root: Path, args: list[str], *, scratch: bool = False
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        env=isolated_environment() if scratch else None,
        check=False,
        capture_output=True,
        timeout=60,
    )


def git_text(root: Path, args: list[str], *, scratch: bool = False) -> str:
    completed = _git(root, args, scratch=scratch)
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", "replace").strip()
        raise GitError(f"git {' '.join(args)} failed: {message}")
    return completed.stdout.decode("utf-8")


def _nul_items(text: str) -> list[str]:
    return [item for item in text.split("\0") if item]


def repo_root(start: Path) -> Path:
    return Path(git_text(start, ["rev-parse", "--show-toplevel"]).strip())


def head_commit(root: Path) -> str | None:
    completed = _git(root, ["rev-parse", "--verify", "--quiet", "HEAD^{commit}"])
    return completed.stdout.decode("utf-8").strip() if completed.returncode == 0 else None


def index_paths(root: Path) -> list[str]:
    return _nul_items(git_text(root, ["ls-files", "-z"]))


def untracked_paths(root: Path) -> list[str]:
    return _nul_items(git_text(root, ["ls-files", "-z", "--others", "--exclude-standard"]))


def tree_paths(root: Path, revision: str | None) -> list[str]:
    if revision is None:
        return []
    return _nul_items(git_text(root, ["ls-tree", "-r", "-z", "--name-only", revision]))


def merge_base(root: Path, ref: str) -> str:
    return git_text(root, ["merge-base", ref, "HEAD"]).strip()


def _parse_changes(text: str) -> list[Change]:
    items = _nul_items(text)
    changes: list[Change] = []
    index = 0
    while index < len(items):
        status = items[index]
        if status.startswith("R"):
            changes.append(Change(path=items[index + 2], old_path=items[index + 1]))
            index += 3
        else:
            changes.append(Change(path=items[index + 1], old_path=None))
            index += 2
    return changes


def diff_changes(root: Path, revisions: list[str]) -> list[Change]:
    args = ["diff", "-z", "-M", "--name-status", "--diff-filter=AR", *revisions]
    return _parse_changes(git_text(root, args))


def staged_changes(root: Path) -> list[Change]:
    base = head_commit(root) or EMPTY_TREE
    return diff_changes(root, ["--cached", base])


def worktree_changes(root: Path) -> list[Change]:
    base = head_commit(root) or EMPTY_TREE
    tracked = diff_changes(root, [base])
    return tracked + [Change(path=path, old_path=None) for path in untracked_paths(root)]


def _replace_tree(source: Path, target: Path) -> None:
    for child in filesystem.iterdir(target):
        if child.name == ".git":
            continue
        if filesystem.is_dir(child) and not filesystem.is_symlink(child):
            filesystem.remove_tree(child)
        else:
            filesystem.unlink(child)
    filesystem.copy_tree(source, target, dirs_exist_ok=True, symlinks=True)


def build_scenario(fixture: Path, workdir: Path) -> Path:
    base, head = fixture / "base", fixture / "head"
    if not base.is_dir() or not head.is_dir():
        raise GitError(f"scenario {fixture} needs base/ and head/ directories")
    repo = workdir / "repo"
    filesystem.copy_tree(base, repo, symlinks=True)
    git_text(repo, ["init", "-q", "-b", "main"], scratch=True)
    git_text(repo, ["add", "-A"], scratch=True)
    tree = git_text(repo, ["write-tree"], scratch=True).strip()
    commit = git_text(
        repo, [*_SCENARIO_IDENTITY, "commit-tree", tree, "-m", "base"], scratch=True
    ).strip()
    git_text(repo, ["update-ref", "HEAD", commit], scratch=True)
    _replace_tree(head, repo)
    git_text(repo, ["add", "-A"], scratch=True)
    return repo

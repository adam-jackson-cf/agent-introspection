"""Define the maintained Python files covered by project quality gates."""

from __future__ import annotations

import subprocess
from pathlib import Path

_EXCLUDED_PREFIXES = (
    ".git/",
    ".mypy_cache/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".venv/",
    "build/",
    "dist/",
    "node_modules/",
    "tests/fixtures/",
    "venv/",
)
_MAINTAINED_PREFIXES = ("scripts/", "src/", "tests/")


def is_excluded_python_path(path: str) -> bool:
    """Return whether a tracked Python path is generated or tool-owned."""
    normalized = path.removeprefix("./")
    return normalized.startswith(_EXCLUDED_PREFIXES) or "/__pycache__/" in f"/{normalized}/"


def is_maintained_python_path(path: str) -> bool:
    """Return whether a tracked Python path belongs to a maintained quality surface."""
    normalized = path.removeprefix("./")
    if not normalized.endswith(".py") or is_excluded_python_path(normalized):
        return False
    return normalized.startswith(_MAINTAINED_PREFIXES) or (
        normalized.startswith(".agents/skills/") and "/scripts/" in normalized
    )


def _git_output(repo_root: Path, args: list[str]) -> bytes:
    """Run one read-only git query in ``repo_root`` and return its stdout."""
    completed = subprocess.run(["git", *args], cwd=repo_root, check=True, stdout=subprocess.PIPE)
    return completed.stdout


def candidate_python_relpaths(repo_root: Path) -> list[str]:
    """Return present Python files git tracks or would add, minus excluded paths."""
    output = _git_output(
        repo_root,
        ["ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "*.py"],
    )
    relpaths = sorted({raw_path.decode("utf-8") for raw_path in output.split(b"\0") if raw_path})
    return [
        relpath
        for relpath in relpaths
        if (repo_root / relpath).is_file() and not is_excluded_python_path(relpath)
    ]


def fixture_location(raw_path: str, root_name: str = "repo") -> tuple[Path, str]:
    """Return a canary fixture's virtual repo root and its repo-relative path.

    The virtual root is the last directory named ``root_name`` above the fixture.
    """
    path = Path(raw_path).resolve()
    indexes = [index for index, part in enumerate(path.parts[:-1]) if part == root_name]
    if not indexes:
        raise SystemExit(f"fixture path has no '{root_name}' directory: {raw_path}")
    root = Path(*path.parts[: indexes[-1] + 1])
    return root, path.relative_to(root).as_posix()


def tracked_python_files() -> list[str]:
    """Return every present tracked Python file or reject an unclassified file."""
    output = _git_output(Path.cwd(), ["ls-files", "-z", "--", "*.py"])
    paths = sorted(raw_path.decode("utf-8") for raw_path in output.split(b"\0") if raw_path)
    paths = [path for path in paths if Path(path).is_file()]
    unclassified = [
        path
        for path in paths
        if not is_excluded_python_path(path) and not is_maintained_python_path(path)
    ]
    if unclassified:
        formatted_paths = "\n".join(f"  - {path}" for path in unclassified)
        raise RuntimeError(
            "Python quality scope has unclassified tracked files; classify each path in "
            f"scripts/lib/python_quality_scope.py:\n{formatted_paths}"
        )
    return [path for path in paths if is_maintained_python_path(path)]

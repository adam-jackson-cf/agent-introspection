from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from lib.python_quality_settings import PROOFS_FILE, TABLE, SettingsError, python_quality

_BUNDLED_EXCLUDED_PREFIXES = (
    ".git/",
    ".mypy_cache/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".venv/",
    "build/",
    "dist/",
    "node_modules/",
    "venv/",
)
FIXTURE_ROOT_NAME = "repo"


@dataclass(frozen=True)
class SourceFile:
    path: Path
    relpath: str
    root: Path


def path_matches_prefix(relpath: str, prefix: str) -> bool:
    normalized_prefix = prefix.rstrip("/")
    return relpath == normalized_prefix or relpath.startswith(f"{normalized_prefix}/")


def _is_bundled_exclusion(normalized: str) -> bool:
    return normalized.startswith(_BUNDLED_EXCLUDED_PREFIXES) or "/__pycache__/" in f"/{normalized}/"


def is_excluded_python_path(path: str) -> bool:
    normalized = path.removeprefix("./")
    if _is_bundled_exclusion(normalized):
        return True
    return any(
        path_matches_prefix(normalized, excluded) for excluded in python_quality().excluded_paths
    )


def is_skill_path(path: str) -> bool:
    return any(path_matches_prefix(path, root) for root in python_quality().skill_roots)


def is_maintained_python_path(path: str) -> bool:
    normalized = path.removeprefix("./")
    if not normalized.endswith(".py") or is_excluded_python_path(normalized):
        return False
    if is_skill_path(normalized):
        return "/scripts/" in normalized
    return any(path_matches_prefix(normalized, root) for root in python_quality().maintained_roots)


def _git_output(repo_root: Path, args: list[str]) -> bytes:
    completed = subprocess.run(
        ["git", *args], cwd=repo_root, check=True, stdout=subprocess.PIPE, timeout=60
    )
    return completed.stdout


def listed_relpaths(repo_root: Path, pathspecs: Sequence[str]) -> list[str]:
    output = _git_output(
        repo_root,
        ["ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *pathspecs],
    )
    return sorted({raw_path.decode("utf-8") for raw_path in output.split(b"\0") if raw_path})


def candidate_python_relpaths(repo_root: Path) -> list[str]:
    return [
        relpath
        for relpath in listed_relpaths(repo_root, ["*.py"])
        if (repo_root / relpath).is_file() and not is_excluded_python_path(relpath)
    ]


def fixture_location(raw_path: str, root_name: str = FIXTURE_ROOT_NAME) -> tuple[Path, str]:
    path = Path(raw_path).resolve()
    indexes = [index for index, part in enumerate(path.parts[:-1]) if part == root_name]
    if not indexes:
        raise SystemExit(
            f"{raw_path}: fixture path has no '{root_name}' directory; "
            f"put the fixture files below a directory named '{root_name}'"
        )
    root = Path(*path.parts[: indexes[-1] + 1])
    return root, path.relative_to(root).as_posix()


def _is_fixture_target(repo_root: Path, target: Path) -> bool:
    if not target.is_relative_to(repo_root):
        return True
    return is_excluded_python_path(f"{target.relative_to(repo_root).as_posix()}/")


def _fixture_sources(target: Path) -> list[SourceFile]:
    files = [target] if target.is_file() else sorted(target.rglob("*.py"))
    sources: list[SourceFile] = []
    for path in files:
        if path.suffix != ".py" or path.is_symlink():
            continue
        root, relpath = fixture_location(str(path))
        if not _is_bundled_exclusion(relpath):
            sources.append(SourceFile(path.resolve(), relpath, root))
    return sources


def select_sources(repo_root: Path, raw_paths: Sequence[str]) -> tuple[list[SourceFile], list[str]]:
    candidates = [
        SourceFile((repo_root / relpath).resolve(), relpath, repo_root)
        for relpath in candidate_python_relpaths(repo_root)
    ]
    if not raw_paths:
        return candidates, []
    sources: dict[Path, SourceFile] = {}
    unmatched: list[str] = []
    for raw_path in raw_paths:
        target = (repo_root / raw_path).resolve()
        if _is_fixture_target(repo_root, target):
            matched = _fixture_sources(target) if target.exists() else []
        else:
            matched = [
                source
                for source in candidates
                if source.path == target or source.path.is_relative_to(target)
            ]
        if not matched:
            unmatched.append(raw_path)
        sources.update((source.path, source) for source in matched)
    return [sources[path] for path in sorted(sources)], unmatched


def report_settings_error(error: SettingsError, label: str) -> None:
    print(f"{label} {error}", file=sys.stderr)


def tracked_python_files() -> list[str]:
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
            "Python quality scope has unclassified tracked files; add each path's root to "
            f"[{TABLE}] maintained_roots or excluded_paths in {PROOFS_FILE}:\n{formatted_paths}"
        )
    return [path for path in paths if is_maintained_python_path(path)]

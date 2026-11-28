from __future__ import annotations

import os
import shutil
import stat
import tomllib
from pathlib import Path
from typing import Any


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def exists(path: Path) -> bool:
    return path.exists()


def is_file(path: Path) -> bool:
    return path.is_file()


def is_dir(path: Path) -> bool:
    return path.is_dir()


def is_symlink(path: Path) -> bool:
    return path.is_symlink()


def is_executable(path: str | Path) -> bool:
    return os.access(path, os.X_OK)


def modified_time(path: Path) -> float:
    return path.stat().st_mtime


def iterdir(path: Path) -> list[Path]:
    return list(path.iterdir())


def glob(path: Path, pattern: str) -> list[Path]:
    return list(path.glob(pattern))


def rglob(path: Path, pattern: str) -> list[Path]:
    return list(path.rglob(pattern))


def mkdir(path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
    path.mkdir(parents=parents, exist_ok=exist_ok)


def unlink(path: Path) -> None:
    path.unlink()


def make_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def remove_tree(path: Path) -> None:
    shutil.rmtree(path)


def copy_tree(
    source: Path, target: Path, *, dirs_exist_ok: bool = False, symlinks: bool = False
) -> None:
    shutil.copytree(source, target, dirs_exist_ok=dirs_exist_ok, symlinks=symlinks)


def copy_file(source: Path, target: Path) -> None:
    shutil.copy(source, target)

from __future__ import annotations

import json
import re
from pathlib import Path

_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


class BunLockError(ValueError):
    __slots__ = ()


def read_bun_lock(path: Path) -> dict[str, object]:
    try:
        data = json.loads(_TRAILING_COMMA.sub(r"\1", path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BunLockError(f"cannot read {path}: {error}") from error
    if not isinstance(data, dict):
        raise BunLockError(f"{path} is not a JSON object")
    return {str(key): value for key, value in data.items()}


def locked_versions(lock: dict[str, object]) -> dict[str, set[str]]:
    packages = lock.get("packages", {})
    if not isinstance(packages, dict):
        raise BunLockError("bun.lock packages is not an object")
    versions: dict[str, set[str]] = {}
    for entry in packages.values():
        if not isinstance(entry, list) or not entry or not isinstance(entry[0], str):
            raise BunLockError("bun.lock package entry has no name@version")
        name, _, version = entry[0].rpartition("@")
        if name and version and ":" not in version:
            versions.setdefault(name, set()).add(version)
    return versions

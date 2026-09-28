"""Content identities of the executing Pipeline package, independent of installation path."""

from __future__ import annotations

import hashlib
from pathlib import Path


def implementation_fingerprint() -> str:
    """Hash the complete executing package, including transitive calculator/emitter code."""
    package = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(package.rglob("*.py")):
        digest.update(path.relative_to(package).as_posix().encode())
        digest.update(b"\x00")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()

"""Write or check the generated sections of the measure and data-gap docs.

Usage: ``uv run python scripts/render-measures.py [--check]``. With ``--check`` the
script exits non-zero when a committed document differs from the registry.
"""

from __future__ import annotations

import sys
from pathlib import Path

from lib.measures_render import BEGIN, DOC, END, GAPS, render_gaps, updated

_EMPTY_DOCUMENT = f"{BEGIN}\n{END}\n"


def _documents() -> dict[Path, str]:
    """Return each generated document refreshed; a missing file starts from an empty block."""

    def current(path: Path) -> str:
        return path.read_text() if path.exists() else _EMPTY_DOCUMENT

    return {
        DOC: updated(current(DOC)),
        GAPS: updated(current(GAPS), render_gaps()),
    }


def main(argv: list[str]) -> int:
    documents = _documents()
    if "--check" in argv:
        stale = [
            path
            for path, text in documents.items()
            if not path.exists() or path.read_text() != text
        ]
        for path in stale:
            print(f"{path.name} is out of date; run scripts/render-measures.py")
        return 1 if stale else 0
    for path, text in documents.items():
        path.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

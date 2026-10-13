"""Write or check the files generated from the signal registry.

They are the generated sections of the measure, data-gap, and producer capability
docs and the dashboard's harness identity module. Usage:
``uv run python scripts/render-measures.py [--check]``. With ``--check`` the script
exits non-zero when a committed file differs from the registry.
"""

from __future__ import annotations

import sys
from pathlib import Path

from lib.capabilities_render import CAPABILITIES_DOC, render_capabilities
from lib.harness_render import HARNESSES_MODULE, render_harnesses
from lib.measures_render import BEGIN, DOC, END, GAPS, render_gaps, updated

_EMPTY_DOCUMENT = f"{BEGIN}\n{END}\n"


def _documents() -> dict[Path, str]:
    """Return each generated file refreshed; a missing doc starts from an empty block."""

    def current(path: Path) -> str:
        return path.read_text() if path.exists() else _EMPTY_DOCUMENT

    return {
        DOC: updated(current(DOC)),
        GAPS: updated(current(GAPS), render_gaps()),
        CAPABILITIES_DOC: updated(current(CAPABILITIES_DOC), render_capabilities()),
        HARNESSES_MODULE: render_harnesses(),
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

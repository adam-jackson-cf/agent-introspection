"""Write or check the generated sections of the measure and data-gap docs.

Usage: ``uv run python scripts/render-measures.py [--check]``. With ``--check`` the
script exits non-zero when a committed document differs from the registry.
"""

from __future__ import annotations

import sys

from lib.measures_render import rendered


def main(argv: list[str]) -> int:
    stale = [path for path, text in rendered().items() if path.read_text() != text]
    if "--check" in argv:
        for path in stale:
            print(f"{path.name} is out of date; run scripts/render-measures.py")
        return 1 if stale else 0
    for path, text in rendered().items():
        path.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

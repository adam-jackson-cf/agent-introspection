from __future__ import annotations

import sys

from lib import filesystem
from lib.harness_render import HARNESSES_MODULE, render_harnesses


def main(argv: list[str]) -> int:
    text = render_harnesses()
    if "--check" in argv:
        if filesystem.exists(HARNESSES_MODULE) and filesystem.read_text(HARNESSES_MODULE) == text:
            return 0
        print(f"{HARNESSES_MODULE.name} is out of date; run scripts/render-measures.py")
        return 1
    filesystem.write_text(HARNESSES_MODULE, text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""Write the NUL-delimited maintained Python file set for shell callers."""

from __future__ import annotations

import subprocess
import sys

from lib.python_quality_scope import tracked_python_files


def main() -> int:
    """Print every maintained tracked Python file, NUL-delimited, on stdout."""
    try:
        paths = tracked_python_files()
        sys.stdout.buffer.write(b"".join(path.encode("utf-8") + b"\0" for path in paths))
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

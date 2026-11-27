#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys

from lib.python_quality_scope import tracked_python_files
from lib.python_quality_settings import SettingsError


def main() -> int:
    try:
        paths = tracked_python_files()
        sys.stdout.buffer.write(b"".join(path.encode("utf-8") + b"\0" for path in paths))
        return 0
    except (OSError, RuntimeError, SettingsError, subprocess.CalledProcessError) as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

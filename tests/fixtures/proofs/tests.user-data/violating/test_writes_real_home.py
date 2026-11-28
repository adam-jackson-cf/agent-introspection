import os
import pwd
from pathlib import Path


def test_a_test_that_writes_under_the_real_home() -> None:
    real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    (real_home / "agent-introspection-user-data-canary.txt").write_text("x", encoding="utf-8")

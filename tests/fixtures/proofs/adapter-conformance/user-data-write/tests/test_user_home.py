import os
from pathlib import Path


def test_settings_land_in_the_home_directory():
    Path(os.path.expanduser("~" + os.environ.get("USER", ""))).joinpath(".agent-introspection-canary").write_text("x")

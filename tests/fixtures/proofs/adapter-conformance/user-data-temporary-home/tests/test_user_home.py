from pathlib import Path


def test_settings_land_in_the_redirected_home(tmp_path):
    (tmp_path / "settings.toml").write_text("x")
    (Path.home() / "settings.toml").write_text("x")

from pathlib import Path


def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(f'[database]\npath = "{tmp_path / "workflow.sqlite3"}"\n')
    return path

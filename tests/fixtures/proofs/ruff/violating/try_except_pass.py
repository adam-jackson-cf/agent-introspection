from pathlib import Path


def remove(path: Path) -> None:
    try:
        path.unlink()
    except Exception:
        pass

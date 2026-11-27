from pathlib import Path


def test_a_test_writes_its_temporary_directory_and_the_redirected_home(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("kept", encoding="utf-8")
    (Path.home() / ".config").mkdir(parents=True, exist_ok=True)
    (Path.home() / ".config" / "note.txt").write_text("kept", encoding="utf-8")

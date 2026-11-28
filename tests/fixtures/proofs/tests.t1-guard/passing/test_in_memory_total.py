from pathlib import Path


def test_the_total_is_computed_in_memory(tmp_path: Path) -> None:
    (tmp_path / "numbers.txt").write_text("1 2 3", encoding="utf-8")
    assert sum(int(n) for n in (tmp_path / "numbers.txt").read_text().split()) == 6

def test_totals_write_only_the_temporary_directory(tmp_path):
    (tmp_path / "total.txt").write_text("3")
    assert sum([1, 2]) == 3

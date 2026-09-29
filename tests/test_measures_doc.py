from scripts.render_measures import DOC, updated


def test_measure_v3_catalog_matches_the_signal_registry() -> None:
    current = DOC.read_text()

    assert updated(current) == current, "run: uv run python scripts/render_measures.py"

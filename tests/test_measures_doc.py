from scripts.render_measures import rendered


def test_generated_docs_match_the_signal_registry() -> None:
    for path, text in rendered().items():
        assert path.read_text() == text, (
            f"run: uv run python scripts/render_measures.py ({path.name})"
        )

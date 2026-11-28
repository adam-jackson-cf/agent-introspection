from lib.harness_render import HARNESSES_MODULE, render_harnesses


def test_generated_harness_module_matches_the_signal_registry() -> None:
    assert HARNESSES_MODULE.read_text() == render_harnesses(), (
        "run: uv run python scripts/render-measures.py"
    )

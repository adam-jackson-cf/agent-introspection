from scripts.lib.capabilities_render import CAPABILITIES_DOC, render_capabilities
from scripts.lib.harness_render import HARNESSES_MODULE, render_harnesses
from scripts.lib.measures_render import rendered, updated


def test_generated_docs_match_the_signal_registry() -> None:
    capabilities_doc = updated(CAPABILITIES_DOC.read_text(), render_capabilities())
    generated = {
        **rendered(),
        CAPABILITIES_DOC: capabilities_doc,
        HARNESSES_MODULE: render_harnesses(),
    }
    for path, text in generated.items():
        assert path.read_text() == text, (
            f"run: uv run python scripts/render-measures.py ({path.name})"
        )

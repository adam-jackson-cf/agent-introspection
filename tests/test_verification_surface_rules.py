from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_SCRIPTS = Path(__file__).parents[1] / "scripts"


def _lib(name: str) -> ModuleType:
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    __import__(f"lib.{name}")
    return sys.modules[f"lib.{name}"]


def _script(name: str) -> ModuleType:
    _lib("test_evidence")
    module_name = name.replace("-", "_")
    spec = importlib.util.spec_from_file_location(module_name, _SCRIPTS / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_SECTIONS = ["Sub-features", "How to get to it (user POV)", "Driving it with the lever", "Gotchas"]


def _surface(checker: ModuleType) -> object:
    return checker.Surface(
        "docs/features",
        tuple(_SECTIONS),
        ("scripts/lever.sh", "lever"),
        ["tests/journeys/test_*.py"],
        ["tests/**/test_*.py"],
    )


def _write(root: Path, path: str, text: str) -> str:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return path


def _document(sections: list[str], link: str) -> str:
    return "# Cache\n\n" + "".join(f"## {title}\n\nSee [it]({link}).\n\n" for title in sections)


def test_a_feature_document_with_the_four_sections_and_a_live_test_link_passes(
    tmp_path: Path,
) -> None:
    checker = _script("check-verification-surface")
    journey = _write(tmp_path, "tests/journeys/test_cache.py", "scripts/lever.sh lever\n")
    document = _write(tmp_path, "docs/features/cache.md", _document(_SECTIONS, "../../" + journey))

    assert checker.feature_map_problems(tmp_path, [document, journey], _surface(checker)) == []


def test_a_missing_section_is_a_feature_map_finding(tmp_path: Path) -> None:
    checker = _script("check-verification-surface")
    journey = _write(tmp_path, "tests/journeys/test_cache.py", "scripts/lever.sh lever\n")
    document = _write(
        tmp_path, "docs/features/cache.md", _document(_SECTIONS[:3], "../../" + journey)
    )

    problems = checker.feature_map_problems(tmp_path, [document], _surface(checker))

    assert len(problems) == 1
    assert problems[0].startswith("docs/features/cache.md: SURFACE.feature-map: H2 sections are")


def test_a_stale_link_is_a_feature_map_finding(tmp_path: Path) -> None:
    checker = _script("check-verification-surface")
    document = _write(
        tmp_path, "docs/features/cache.md", _document(_SECTIONS, "../../tests/gone.py")
    )

    problems = checker.feature_map_problems(tmp_path, [document], _surface(checker))

    assert any("link ../../tests/gone.py does not resolve" in problem for problem in problems)


def test_headings_inside_code_fences_are_ignored() -> None:
    checker = _script("check-verification-surface")
    text = "## Sub-features\n```\n## Not a heading\n```\n## Gotchas\n"

    assert [title for _, title in checker._headings(text)] == ["Sub-features", "Gotchas"]


def test_a_journey_that_skips_the_lever_is_a_finding(tmp_path: Path) -> None:
    checker = _script("check-verification-surface")
    journey = _write(
        tmp_path, "tests/journeys/test_cache.py", "def test_cache_loads():\n    pass\n"
    )

    problems = checker.journey_problems(tmp_path, [journey], _surface(checker))

    assert problems == [
        "tests/journeys/test_cache.py: SURFACE.journeys: journey does not drive the lever; "
        "run scripts/lever.sh lever from it"
    ]


def test_a_test_driving_the_lever_outside_the_pattern_is_a_finding(tmp_path: Path) -> None:
    checker = _script("check-verification-surface")
    test = _write(tmp_path, "tests/test_cache.py", "LEVER = 'scripts/lever.sh'\n")

    problems = checker.journey_problems(tmp_path, [test], _surface(checker))

    assert len(problems) == 1
    assert "drives the lever outside the journey pattern" in problems[0]

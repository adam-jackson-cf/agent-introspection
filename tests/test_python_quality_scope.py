from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

SCRIPTS = Path(__file__).parents[1] / "scripts"


@pytest.fixture(scope="module")
def quality_scope() -> Iterator[ModuleType]:
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(SCRIPTS))
        spec = importlib.util.spec_from_file_location(
            "python_quality_scope", SCRIPTS / "lib/python_quality_scope.py"
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        patch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        yield module


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/agent_introspection/session_context.py", True),
        ("tests/test_session_context.py", True),
        ("scripts/lib/python_quality_scope.py", True),
        (
            ".agents/skills/introspection-onboarding/scripts/tool.py",
            True,
        ),
        (".agents/skills/introspection-onboarding/tool.py", False),
        ("tests/fixtures/proofs/python.quality/PYQ100/passing/repo/src/pkg/mod.py", False),
        ("docs/reference/generated.py", False),
        (".ruff_cache/0.15.0/cache.py", False),
    ],
)
def test_maintained_python_scope_is_explicit(
    quality_scope: ModuleType, path: str, expected: bool
) -> None:
    assert quality_scope.is_maintained_python_path(path) is expected


def test_excluded_paths_come_from_the_settings_table(quality_scope: ModuleType) -> None:
    assert quality_scope.is_excluded_python_path("tests/fixtures/x.py")
    assert not quality_scope.is_excluded_python_path("tests/test_x.py")

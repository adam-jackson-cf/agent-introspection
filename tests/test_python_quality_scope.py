from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture(scope="module")
def quality_scope() -> ModuleType:
    source = Path(__file__).parents[1] / "scripts/python_quality_scope.py"
    spec = importlib.util.spec_from_file_location("python_quality_scope", source)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/agent_introspection/session_context.py", True),
        ("tests/test_session_context.py", True),
        ("scripts/python_quality_scope.py", True),
        (
            ".agents/skills/introspection-onboarding/scripts/adapters/codex-cli/adapter.py",
            True,
        ),
        ("docs/reference/generated.py", False),
        (".ruff_cache/0.15.0/cache.py", False),
    ],
)
def test_maintained_python_scope_is_explicit(
    quality_scope: ModuleType, path: str, expected: bool
) -> None:
    assert quality_scope.is_maintained_python_path(path) is expected

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.support.script_loader import CANARIES, REPO_ROOT, load_script

checker = load_script("check-test-mocking")
CASES = CANARIES / "tests.mocking"


def _problems(relative: str) -> list[str]:
    layout = checker.load_layout(REPO_ROOT)
    path = CASES / relative
    problems: list[str] = checker.check_python(layout, path, relative)
    return problems


@pytest.mark.parametrize(
    ("relative", "target"),
    [
        ("cases/test_dotted_target.py", "agent_introspection.findings.refresh"),
        ("cases/test_imported_object.py", "agent_introspection.findings.refresh"),
        ("cases/test_assignment.py", "agent_introspection.findings.refresh"),
        ("cases/test_spec.py", "agent_introspection.config.AppConfig"),
        ("violating/test_patches_owned_code.py", "agent_introspection.spool.sync"),
    ],
)
def test_patching_owned_code_is_reported_with_its_target(relative: str, target: str) -> None:
    (problem,) = _problems(relative)
    assert "TEST.no-self-mocking" in problem
    assert target in problem
    assert "owned code" in problem


def test_faking_only_a_declared_adapter_seam_is_clean() -> None:
    assert _problems("passing/test_fakes_a_seam.py") == []


def test_a_third_party_patch_is_reported(tmp_path: Path) -> None:
    layout = checker.load_layout(REPO_ROOT)
    source = tmp_path / "test_x.py"
    source.write_text(
        "import urllib.request\n\n\ndef test_x(monkeypatch):\n"
        "    monkeypatch.setattr(urllib.request, 'urlopen', None)\n",
        encoding="utf-8",
    )
    (problem,) = checker.check_python(layout, source, "test_x.py")
    assert "urllib.request.urlopen (a third party" in problem


def test_a_declared_seam_that_does_not_exist_is_reported() -> None:
    layout = checker.load_layout(REPO_ROOT)
    broken = replace(layout, seams=frozenset({"agent_introspection.spool.no_such_seam"}))
    (problem,) = checker.seam_problems(broken)
    assert "agent_introspection.spool:no_such_seam" in problem


def test_an_empty_seam_list_allows_no_substitution() -> None:
    layout = replace(checker.load_layout(REPO_ROOT), seams=frozenset())
    path = CASES / "passing" / "test_fakes_a_seam.py"
    (problem,) = checker.check_python(layout, path, "test_fakes_a_seam.py")
    assert "agent_introspection.spool.host_name" in problem

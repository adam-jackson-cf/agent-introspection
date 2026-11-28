from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.support.script_loader import CANARIES, REPO_ROOT, load_script

mocking = load_script("check-test-mocking")
evasion = load_script("check-test-evasion")
hooks = load_script("check-test-hooks")
growth = load_script("check-test-growth")
settings = sys.modules["lib.proofs_settings"]
adapter = sys.modules["lib.pytest_adapter"]
_CONFORMANCE = CANARIES / "adapter-conformance"
_IN_MEMORY = frozenset({"source", "static-cases"})


def _case(path: Path) -> dict[str, Any]:
    case: dict[str, Any] = json.loads((path / "case.json").read_text(encoding="utf-8"))
    return case


def _cases() -> list[Path]:
    return sorted(path for path in _CONFORMANCE.iterdir() if (path / "case.json").is_file())


def _sources(case: Path) -> list[str]:
    return sorted(path.relative_to(case).as_posix() for path in case.rglob("*.py"))


def _read(case: Path, relative: str) -> str:
    return (case / relative).read_text(encoding="utf-8")


def _proofs(case: Path) -> dict[str, Any]:
    document: dict[str, Any] = (
        settings.load_proofs(case) if (case / "proofs.toml").is_file() else {}
    )
    return document


def _self_mocking(case: Path) -> list[str]:
    layout = mocking.load_layout(REPO_ROOT)
    return [
        problem
        for relative in _sources(case)
        for problem in mocking.check_python(layout, case / relative, relative)
    ]


def _evasion(case: Path) -> list[str]:
    findings = [
        finding
        for relative in _sources(case)
        for finding in evasion.python_findings(relative, _read(case, relative))
    ]
    return [finding.render() for finding in evasion.unapproved(findings, _proofs(case))]


def _test_hooks(case: Path) -> list[str]:
    layout = settings.table(settings.load_proofs(REPO_ROOT), "layout")
    sources = _sources(case)
    scope = hooks.Scope(case, sources, [], sources, hooks.marker_tokens(layout))
    return [problem for relative in sources for problem in hooks.python_findings(scope, relative)]


def _growth(case: Path) -> list[str]:
    cases = [
        static
        for relative in _sources(case)
        for static in adapter.static_cases(relative, _read(case, relative))
    ]
    problems: list[str] = growth._growth(cases)
    return problems


_OWNERS = {
    "TEST.no-self-mocking": _self_mocking,
    "TEST.no-evasion": _evasion,
    "TEST.no-test-hooks": _test_hooks,
    "TEST.growth.justified": _growth,
}


def _in_memory_cases(owned: bool) -> list[Path]:
    return [
        path
        for path in _cases()
        if _case(path)["evidence"] in _IN_MEMORY and (_case(path)["rule"] in _OWNERS) is owned
    ]


def _not_applicable() -> set[str]:
    entries = settings.load_proofs(REPO_ROOT).get("not_applicable", [])
    return {str(entry.get("rule")) for entry in entries if isinstance(entry, dict)}


@pytest.mark.parametrize("case", _in_memory_cases(owned=True), ids=lambda path: path.name)
def test_an_in_memory_conformance_case_gets_its_verdict_from_the_owning_checker(
    case: Path,
) -> None:
    spec = _case(case)

    findings = _OWNERS[spec["rule"]](case)

    if spec["expect"] == "finding":
        assert findings
        assert all(f": {spec['rule']}: " in finding for finding in findings), findings
    else:
        assert spec["expect"] == "clean"
        assert findings == []


def test_a_conformance_case_with_no_owning_checker_is_an_approved_exclusion() -> None:
    unowned = {_case(path)["rule"] for path in _in_memory_cases(owned=False)}

    assert unowned == {"TEST.property-seed"}
    assert unowned <= _not_applicable()


def test_every_conformance_case_names_a_known_evidence_layer() -> None:
    layers = {_case(path)["evidence"] for path in _cases()}

    assert len(_cases()) == 28
    assert layers == {"source", "static-cases", "run-report", "collection"}

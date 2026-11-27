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
    _lib("run_evidence")
    module_name = name.replace("-", "_")
    spec = importlib.util.spec_from_file_location(module_name, _SCRIPTS / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_BASE = {"run": {"omit": ["*/cli.py"]}, "report": {"fail_under": 85}}


def test_raising_the_floor_needs_no_approval() -> None:
    ratchet = _script("check-coverage-ratchet")

    assert (
        ratchet.config_changes(_BASE, {"run": {"omit": ["*/cli.py"]}, "report": {"fail_under": 90}})
        == []
    )


def test_lowering_or_removing_the_floor_is_a_change_needing_approval() -> None:
    ratchet = _script("check-coverage-ratchet")

    lowered = ratchet.config_changes(
        _BASE, {"run": {"omit": ["*/cli.py"]}, "report": {"fail_under": 80}}
    )
    removed = ratchet.config_changes(_BASE, {"run": {"omit": ["*/cli.py"]}})

    assert lowered == ["[tool.coverage.report] fail_under lowered from 85 to 80"]
    assert removed == ["[tool.coverage.report] fail_under lowered from 85 to None"]


def test_a_new_omit_entry_is_a_change_needing_approval() -> None:
    ratchet = _script("check-coverage-ratchet")
    after = {"run": {"omit": ["*/cli.py", "*/schedule.py"]}, "report": {"fail_under": 85}}

    assert ratchet.config_changes(_BASE, after) == ["[tool.coverage.run] omit gained */schedule.py"]


def test_pragma_patterns_find_python_and_script_exclusions() -> None:
    ratchet = _script("check-coverage-ratchet")
    patterns = ratchet.pragma_patterns({"report": {"exclude_also": ["if TYPE_CHECKING:"]}})
    pragma = "# pragma" + ": no cover"
    lines = [f"x = 1  {pragma}", "/* istanbul" + " ignore next */", "if TYPE_CHECKING:", "x = 2"]

    flagged = [line for line in lines if any(pattern.search(line) for pattern in patterns)]

    assert flagged == lines[:3]


def test_coverage_table_reads_only_tool_coverage() -> None:
    ratchet = _script("check-coverage-ratchet")
    text = "[tool.coverage.report]\nfail_under = 85\n[tool.ruff]\nline-length = 100\n"

    assert ratchet.coverage_table(text) == {"report": {"fail_under": 85}}

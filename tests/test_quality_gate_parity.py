from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPTS = Path(__file__).parents[1] / "scripts"


def _load(name: str) -> ModuleType:
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), _SCRIPTS / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parity = _load("check-quality-gate-parity")
_FIXTURES = Path(__file__).parent / "fixtures" / "proofs" / "proofs.gate"


def _repo(tmp_path: Path, name: str = "parity-matched") -> Path:
    root = tmp_path / "repo"
    shutil.copytree(_FIXTURES / name, root)
    return root


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def _rules(problems: list[str]) -> list[str]:
    return [problem.split(": ")[1] for problem in problems]


def test_matched_points_pass(tmp_path: Path) -> None:
    assert parity.check(_repo(tmp_path)) == []


def test_hook_only_check_breaks_parity(tmp_path: Path) -> None:
    problems = parity.check(_repo(tmp_path, "parity-divergent"))

    assert _rules(problems) == ["GATE.parity"]
    assert "hook verify runs 'uv run tool verify'" in problems[0]


def test_unowned_proofs_file_breaks_the_self_check(tmp_path: Path) -> None:
    problems = parity.check(_repo(tmp_path, "unowned"))

    assert _rules(problems) == ["GATE.self-check"]
    assert "proofs.toml has no code owner" in problems[0]


def test_validator_needs_a_ci_job_of_its_own(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _edit(
        root / "proofs.toml",
        '\n[[claim.enforced_by]]\npoint = "ci"\nfile = ".github/workflows/ci.yml"\n'
        'ref = "scripts/gate.sh proofs"\nstatus = "proofs"\n',
        "",
    )

    problems = parity.check(root)

    assert _rules(problems) == ["GATE.self-check"]
    assert "runs the validator as its own job" in problems[0]


@pytest.mark.parametrize("suffix", [" || true", " ; true", " && true", " | cat"])
def test_masked_ci_invocation_breaks_parity(tmp_path: Path, suffix: str) -> None:
    root = _repo(tmp_path)
    workflow = root / ".github/workflows/ci.yml"
    _edit(workflow, "run: bash scripts/gate.sh static", f"run: bash scripts/gate.sh static{suffix}")

    problems = parity.check(root)

    assert any("job static does not run exactly one unmasked" in item for item in problems)


def test_aggregate_must_always_run_and_test_each_tier(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    workflow = root / ".github/workflows/ci.yml"
    _edit(workflow, "    if: ${{ always() }}\n", "")
    _edit(workflow, 'test "${{ needs.static.result }}" = success', "true")

    problems = parity.check(root)

    assert any("skips after a failed tier" in item for item in problems)
    assert any("does not fail when static fails" in item for item in problems)


def test_hook_mode_without_a_ci_job_breaks_parity(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _edit(root / "scripts/gate.sh", "hook_modes=(static)", "hook_modes=(static t2)")

    problems = parity.check(root)

    assert any("the hook runs t2 but no CI job" in item for item in problems)


def test_points_of_one_claim_share_an_entrypoint(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _edit(root / "proofs.toml", 'ref = "scripts/gate.sh hook"', 'ref = "scripts/other.sh hook"')

    problems = parity.check(root)

    assert any("points invoke scripts/gate.sh, scripts/other.sh" in item for item in problems)


def test_commented_hook_entry_does_not_count(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _edit(
        root / ".pre-commit-config.yaml",
        "entry: bash scripts/gate.sh hook",
        "entry: echo  # scripts/gate.sh hook",
    )

    problems = parity.check(root)

    assert any("hook gate runs 'echo'" in item for item in problems)

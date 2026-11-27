from __future__ import annotations

import importlib.util
import shutil
import sys
import tomllib
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


validator = _load("validate-rule-coverage")
_FIXTURES = Path(__file__).parent / "fixtures" / "proofs" / "proofs.gate"


def _proofs(root: Path) -> dict[str, object]:
    return tomllib.loads((root / "proofs.toml").read_text(encoding="utf-8"))


def _copy(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    shutil.copytree(_FIXTURES / name, root)
    return root


def test_bound_fixture_has_a_valid_structure(tmp_path: Path) -> None:
    root = _copy(tmp_path, "bound")

    assert validator.validate(root, _proofs(root), structure_only=True) == ([], [])


def test_unbound_reference_is_reported_against_the_claim(tmp_path: Path) -> None:
    root = _copy(tmp_path, "unbound")

    errors, _ = validator.validate(root, _proofs(root), structure_only=True)

    assert errors == ["check: ci.yml lacks ref: sh absent.sh"]


def test_ci_point_requires_a_self_check_claim(tmp_path: Path) -> None:
    root = _copy(tmp_path, "bound")
    proofs = _proofs(root)
    proofs["claim"][0]["rules"] = ["A.rule"]

    errors, _ = validator.validate(root, proofs, structure_only=True)

    assert errors == ["validator: no claim with a ci point covers GATE.self-check"]


def test_unknown_top_level_key_is_rejected(tmp_path: Path) -> None:
    root = _copy(tmp_path, "bound")
    proofs = {**_proofs(root), "ledger": {}}

    errors, _ = validator.validate(root, proofs, structure_only=True)

    assert errors == ["unknown top-level key: ledger"]


def test_catalogue_rule_needs_exactly_one_owner(tmp_path: Path) -> None:
    root = _copy(tmp_path, "bound")
    catalog = {
        "schema_version": 1,
        "rules": [
            {"id": "GATE.self-check", "kind": "process", "applicability": "always"},
            {"id": "B.rule", "kind": "process", "applicability": "always"},
        ],
    }

    errors, _ = validator.validate(root, _proofs(root), catalog, structure_only=True)

    assert errors == ["missing rule: B.rule"]


def test_main_prints_the_structure_only_result(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _copy(tmp_path, "bound")

    assert validator.main(["--root", str(root), "--structure-only"]) == 0
    assert capsys.readouterr().out == "proofs STRUCTURE_ONLY: 1 claims, 0 exclusions\n"


def test_missing_root_exits_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert validator.main(["--root", str(tmp_path / "absent")]) == 2
    assert capsys.readouterr().err == "proofs FAIL: target root is missing\n"

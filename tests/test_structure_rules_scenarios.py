from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.support.script_loader import load_script

pytestmark = pytest.mark.boundary

_SCENARIOS = Path(__file__).parent / "fixtures" / "proofs" / "structure"


@pytest.fixture(scope="module")
def checker() -> ModuleType:
    return load_script("check-structure")


@pytest.fixture(scope="module")
def git() -> ModuleType:
    load_script("check-structure")
    return sys.modules["lib.structure.repo_git"]


_VIOLATIONS = {
    "reversed-import": "STRUCT.dependency-direction: rates (core) imports cli (interface)",
    "unmapped-module": "STRUCT.dependency-direction: module part 'orders' has no tier",
    "sibling-import": "STRUCT.sibling-isolation: invoice imports its 'service' sibling billing",
    "lone-tier": "STRUCT.tier-readership: tier 'store' is read by 1 part(s) (billing)",
    "one-reader": "STRUCT.single-owner-placement: 'rounding' sits in shared tier 'core'",
    "misplaced-file": "deploy/run.sh: STRUCT.folder-placement: no [structure].owners entry",
    "grab-bag-folder": "src/shop/misc: STRUCT.folder-placement: directory 'misc' is a grab-bag",
    "banned-word": "docs/utils.md: NAME.conventions: 'utils' is a banned word",
    "child-repeats-parent": "NAME.conventions: 'billing-notes' repeats its parent 'billing'",
    "version-suffix": "NAME.conventions: 'publish-v2' carries a version token",
    "rename-residue": "docs/guide.md:3: RENAME.residue: retired name 'export-report'",
    "prefix-cluster-member": "scripts/check-gamma.sh: PREFIX.candidates: 'check-gamma.sh' joins",
    "ts-reversed-import": "STRUCT.dependency-direction: web/rates (contract) imports web/main",
    "ts-sibling-import": "STRUCT.sibling-isolation: web/invoice imports its 'view' sibling",
}


@pytest.mark.parametrize("name", ["compliant", "ts-compliant"])
def test_compliant_scenario_passes(
    checker: ModuleType, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    assert checker.main(["--scenario", str(_SCENARIOS / name)]) == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(("name", "expected"), sorted(_VIOLATIONS.items()))
def test_violating_scenario_fails_with_one_line_per_finding(
    checker: ModuleType, capsys: pytest.CaptureFixture[str], name: str, expected: str
) -> None:
    assert checker.main(["--scenario", str(_SCENARIOS / name)]) == 1
    output = capsys.readouterr().out.splitlines()
    assert any(expected in line for line in output)
    assert all("; " in line for line in output)


def _scenario_repo(git: ModuleType, tmp_path: Path, name: str) -> Path:
    return Path(git.build_scenario(_SCENARIOS / name, tmp_path))


def test_owner_exception_releases_a_prefix_cluster(
    checker: ModuleType, git: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _scenario_repo(git, tmp_path, "prefix-cluster-member")
    settings = repo / "proofs.toml"
    settings.write_text(
        settings.read_text(encoding="utf-8").replace(
            "[layout]",
            '[[exception]]\nrule = "PREFIX.candidates"\npath = "scripts/check-*"\n'
            'reason = "Checks share one runner."\napproved_by = "owner"\n\n[layout]',
        ),
        encoding="utf-8",
    )
    scope, findings = checker.judge(repo, "staged", [])
    assert "scripts/check-gamma.sh" in scope.judged
    assert findings == []
    capsys.readouterr()


def test_merge_base_event_judges_branch_additions_only(
    checker: ModuleType, git: ModuleType, tmp_path: Path
) -> None:
    repo = _scenario_repo(git, tmp_path, "banned-word")
    identity = ["-c", "user.name=t", "-c", "user.email=t@localhost"]
    git.git_text(repo, ["branch", "-f", "base-line", "HEAD"])
    git.git_text(repo, [*identity, "commit", "-q", "-m", "head"])
    settings = repo / "proofs.toml"
    settings.write_text(
        settings.read_text(encoding="utf-8").replace("origin/main", "base-line"), encoding="utf-8"
    )
    scope, findings = checker.judge(repo, "merge-base", [])
    assert scope.judged == ("docs/utils.md",)
    assert [finding.rule for finding in findings] == ["NAME.conventions"]


def test_full_scan_judges_legacy_paths_and_explicit_paths_judge_given_files(
    checker: ModuleType, git: ModuleType, tmp_path: Path
) -> None:
    repo = _scenario_repo(git, tmp_path, "prefix-cluster-member")
    _, staged = checker.judge(repo, "staged", [])
    _, full = checker.judge(repo, None, [])
    _, explicit = checker.judge(repo, None, [str(repo / "scripts" / "check-alpha.sh")])
    assert [finding.path for finding in staged] == ["scripts/check-gamma.sh"]
    assert {finding.path for finding in full} >= {"scripts/check-alpha.sh", "scripts/check-beta.sh"}
    assert [finding.path for finding in explicit] == ["scripts/check-alpha.sh"]


def test_missing_base_ref_fails_instead_of_skipping(
    checker: ModuleType, git: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _scenario_repo(git, tmp_path, "compliant")
    with pytest.raises(git.GitError, match="merge-base"):
        checker.judge(repo, "merge-base", [])
    capsys.readouterr()


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@localhost", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout


def test_scenario_repo_ignores_a_hook_exported_git_location(
    git: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outer = tmp_path / "outer"
    outer.mkdir()
    _git(outer, "init", "-q")
    _git(outer, "commit", "-q", "--allow-empty", "-m", "outer")
    before = (outer / ".git" / "HEAD").read_text(encoding="utf-8")
    monkeypatch.setenv("GIT_DIR", str(outer / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(outer / ".git" / "index"))
    repo = git.build_scenario(_SCENARIOS / "compliant", tmp_path / "work")
    assert (repo / ".git").is_dir()
    assert (outer / ".git" / "HEAD").read_text(encoding="utf-8") == before
    monkeypatch.delenv("GIT_DIR")
    monkeypatch.delenv("GIT_INDEX_FILE")
    assert _git(outer, "log", "--format=%s").split() == ["outer"]

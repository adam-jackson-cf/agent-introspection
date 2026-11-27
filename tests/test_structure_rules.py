from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPTS = Path(__file__).parents[1] / "scripts"
_SCENARIOS = Path(__file__).parent / "fixtures" / "proofs" / "structure"


def _load(name: str, path: Path) -> ModuleType:
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def checker() -> ModuleType:
    return _load("check_structure", _SCRIPTS / "check-structure.py")


@pytest.fixture(scope="module")
def paths() -> ModuleType:
    _load("check_structure", _SCRIPTS / "check-structure.py")
    return sys.modules["lib.structure.path_rules"]


@pytest.fixture(scope="module")
def settings_module() -> ModuleType:
    _load("check_structure", _SCRIPTS / "check-structure.py")
    return sys.modules["lib.structure.settings"]


@pytest.fixture(scope="module")
def git() -> ModuleType:
    _load("check_structure", _SCRIPTS / "check-structure.py")
    return sys.modules["lib.structure.repo_git"]


def _scenario_root(name: str) -> Path:
    return _SCENARIOS / name / "base"


@pytest.mark.parametrize(
    ("name", "tokens"),
    [
        ("log_audit", ["log", "audit"]),
        ("HTTPServer", ["http", "server"]),
        ("parserV2.py", ["parser", "v2", "py"]),
        ("install-signoz", ["install", "signoz"]),
        ("__init__", ["init"]),
    ],
)
def test_name_tokens_split_on_separators_and_case(
    paths: ModuleType, name: str, tokens: list[str]
) -> None:
    assert paths.name_tokens(name) == tokens


@pytest.mark.parametrize(
    ("parent", "child", "repeats"),
    [
        ("audit", "log_audit", True),
        ("rail", "railways", False),
        ("audit-log", "log-audit", False),
        ("audit-log", "audit_log_tokens", True),
        ("signoz", "install-signoz", True),
    ],
)
def test_child_repeats_parent_matches_whole_ordered_tokens(
    paths: ModuleType, parent: str, child: str, repeats: bool
) -> None:
    assert paths.repeats_parent(parent, child) is repeats


@pytest.mark.parametrize(
    ("name", "corrected"),
    [("parser-v2", "parser"), ("v3_loader", "loader"), ("v2", None), ("sha256", None)],
)
def test_version_tokens_are_rejected_outside_standalone_folders(
    paths: ModuleType, name: str, corrected: str | None
) -> None:
    assert paths.unversioned(name) == corrected


@pytest.mark.parametrize(
    ("pattern", "path", "matches"),
    [
        ("scripts/*", "scripts/check-structure.py", True),
        ("scripts/*", "scripts/lib/x.py", False),
        ("assets/**", "assets/a/b.png", True),
        ("references/*.md", "references/deep/x.md", False),
    ],
)
def test_glob_star_stays_in_one_segment(
    paths: ModuleType, pattern: str, path: str, matches: bool
) -> None:
    assert paths.glob_match(pattern, path) is matches


def test_cluster_key_strips_the_test_affix_and_skips_dated_records(
    paths: ModuleType, settings_module: ModuleType
) -> None:
    settings = settings_module.load_settings(_scenario_root("compliant"))
    assert paths.cluster_key(settings, "test_cli_spool.py") == "cli"
    assert paths.cluster_key(settings, "test_cli.py") is None
    assert paths.cluster_key(settings, "check-structure.py") == "check"
    assert paths.cluster_key(settings, "2026-09-30-ux-review.md") is None


def _write_settings(tmp_path: Path, replace: tuple[str, str]) -> Path:
    text = (_scenario_root("compliant") / "proofs.toml").read_text(encoding="utf-8")
    assert replace[0] in text
    (tmp_path / "proofs.toml").write_text(text.replace(*replace), encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    ("replace", "message"),
    [
        (('base_ref = "origin/main"', 'base_ref = "origin/main"\nextra = 1'), "unknown keys"),
        (('money"]', 'money", "billing"]'), "in two tiers"),
        (('fixtures_dir = "tests/fixtures"', 'fixtures = "tests/fixtures"'), "fixtures_dir"),
        (('"scripts/*" = "kebab-case"', '"scripts/*" = "camel"'), "unknown casings"),
    ],
)
def test_settings_reject_unknown_or_ambiguous_values(
    settings_module: ModuleType, tmp_path: Path, replace: tuple[str, str], message: str
) -> None:
    root = _write_settings(tmp_path, replace)
    with pytest.raises(settings_module.SettingsError, match=message):
        settings_module.load_settings(root)


def test_exception_covers_its_rule_check_and_path_only(settings_module: ModuleType) -> None:
    entry = settings_module.StructureException(
        rule="NAME.conventions", path="docs/billing", check="child-repeats-parent"
    )
    assert entry.covers("NAME.conventions", "docs/billing/billing-notes.md", "child-repeats-parent")
    assert not entry.covers("NAME.conventions", "docs/billing/billing-notes.md", "banned-word")
    assert not entry.covers("NAME.conventions", "docs/billings/x.md", "child-repeats-parent")
    cluster = settings_module.StructureException(
        rule="PREFIX.candidates", path="scripts/check-*", check=None
    )
    assert cluster.covers("PREFIX.candidates", "scripts/check-gamma.sh", "cluster")
    assert not cluster.covers("STRUCT.folder-placement", "scripts/check-gamma.sh", "owner")


def test_settings_reject_unknown_exception_fields(
    settings_module: ModuleType, tmp_path: Path
) -> None:
    root = _write_settings(
        tmp_path,
        (
            "[layout]",
            '[[exception]]\nrule = "PREFIX.candidates"\npath = "scripts/check-*"\n'
            'reason = "r"\napproved_by = "owner"\nscope = "all"\n\n[layout]',
        ),
    )
    with pytest.raises(settings_module.SettingsError, match="unknown keys: scope"):
        settings_module.load_settings(root)


def test_retired_names_keep_unchanged_segments(git: ModuleType) -> None:
    rename = sys.modules["lib.structure.rename_rules"]
    change = git.Change(path="scripts/gate/effort_gate.py", old_path="scripts/gate/gate_effort.py")
    assert rename.retired_names(change) == [("gate_effort", "effort_gate")]


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
}


@pytest.mark.boundary
def test_compliant_scenario_passes(checker: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
    assert checker.main(["--scenario", str(_SCENARIOS / "compliant")]) == 0
    assert capsys.readouterr().out == ""


@pytest.mark.boundary
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


@pytest.mark.boundary
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


@pytest.mark.boundary
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


@pytest.mark.boundary
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


@pytest.mark.boundary
def test_missing_base_ref_fails_instead_of_skipping(
    checker: ModuleType, git: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _scenario_repo(git, tmp_path, "compliant")
    with pytest.raises(git.GitError, match="merge-base"):
        checker.judge(repo, "merge-base", [])
    capsys.readouterr()

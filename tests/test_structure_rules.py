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
        (('source_root = "src"', 'source_root = "src"\nextra = 1'), "unknown keys"),
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


def test_typescript_settings_map_module_paths_to_their_tiers(settings_module: ModuleType) -> None:
    settings = settings_module.load_settings(_SCENARIOS / "ts-compliant" / "head")
    imports = sys.modules["lib.structure.typescript_imports"]

    assert settings.typescript.layering.tier_of["web/format"] == "contract"
    assert imports.part_of(settings, "web/view.ts") == "web/view"
    assert imports.part_of(settings, "web/view.test.ts") is None
    assert imports.part_of(settings, "web/shapes.d.ts") is None
    assert imports.part_of(settings, "src/shop/api.py") is None


def test_typescript_settings_reject_unknown_keys(
    settings_module: ModuleType, tmp_path: Path
) -> None:
    text = (_SCENARIOS / "ts-compliant" / "head" / "proofs.toml").read_text(encoding="utf-8")
    (tmp_path / "proofs.toml").write_text(
        text.replace('tsconfig = "tsconfig.json"', 'tsconfig = "tsconfig.json"\nroots = []'),
        encoding="utf-8",
    )
    with pytest.raises(
        settings_module.SettingsError, match=r"\[structure\.typescript\] has unknown"
    ):
        settings_module.load_settings(tmp_path)


def test_a_module_inside_its_one_readers_folder_already_sits_under_its_owner(
    settings_module: ModuleType,
) -> None:
    rules = sys.modules["lib.structure.import_rules"]
    layering = settings_module.Layering(
        "[structure.typescript].tier_modules",
        ("model", "entry"),
        {"web/server/assets": "model", "web/rates": "model", "web/server": "entry"},
    )
    graph = rules.ImportGraph(
        part_files={
            "web/server/assets": ("web/server/assets.ts",),
            "web/rates": ("web/rates.ts",),
            "web/server": ("web/server.ts",),
        },
        edges=(
            rules.Edge("web/server", "web/server/assets", "web/server.ts", 1),
            rules.Edge("web/server", "web/rates", "web/server.ts", 2),
        ),
        unparsable=(),
    )

    findings = rules.import_findings(layering, graph, {"web/server/assets", "web/rates"})

    owners = [finding.path for finding in findings if finding.rule == rules.SINGLE_OWNER]
    assert owners == ["web/rates.ts"]

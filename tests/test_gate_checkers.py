from __future__ import annotations

import importlib.util
import json
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


lock_parity = _load("check-lock-parity")
skill_metadata = _load("check-skill-metadata")
install_guidance = _load("check-install-guidance")
hook_installed = _load("check-hook-installed")
_SCHEMA = Path(__file__).parents[1] / ".agents" / "skill-metadata.schema.json"


def _bun_project(root: Path, **package: object) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "package.json"
    body = {"name": "app", "dependencies": {"a": "1.0.0"}, **package}
    manifest.write_text(json.dumps(body), encoding="utf-8")
    lock = {
        "lockfileVersion": 1,
        "workspaces": {"": {"name": "app", "dependencies": {"a": "1.0.0"}}},
    }
    (root / "bun.lock").write_text(json.dumps(lock), encoding="utf-8")
    return manifest


def test_pinned_bun_project_passes(tmp_path: Path) -> None:
    manifest = _bun_project(tmp_path / "app", packageManager="bun@1.3.0")

    assert lock_parity.check_files([manifest]) == []


def test_missing_pin_and_foreign_lock_fail_package_manager(tmp_path: Path) -> None:
    manifest = _bun_project(tmp_path / "app")
    (tmp_path / "app/yarn.lock").write_text("", encoding="utf-8")

    problems = lock_parity.check_files([manifest])

    assert len(problems) == 2
    assert all("PARITY.package-manager" in problem for problem in problems)


def test_dependency_free_manifest_needs_no_lock(tmp_path: Path) -> None:
    manifest = tmp_path / "package.json"
    manifest.write_text('{"name": "plugin"}', encoding="utf-8")

    assert lock_parity.check_files([manifest]) == []


def test_mise_and_ci_must_read_the_package_manager_pin(tmp_path: Path) -> None:
    manifest = _bun_project(tmp_path / "app", packageManager="bun@1.3.0")
    mise = tmp_path / "mise.toml"
    mise.write_text('[tools]\nbun = "1.4.4"\n', encoding="utf-8")
    workflow = tmp_path / ".github/workflows/ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "jobs:\n  a:\n    steps:\n      - uses: oven-sh/setup-bun@v2\n"
        "        with:\n          bun-version: 1.3.0\n",
        encoding="utf-8",
    )

    problems = lock_parity.check_files([manifest, mise, workflow])

    assert any("bun 1.4.4 differs" in problem for problem in problems)
    assert any("use bun-version-file" in problem for problem in problems)


def test_skill_frontmatter_must_match_the_schema(tmp_path: Path) -> None:
    schema = skill_metadata.load_schema(_SCHEMA)
    skill = tmp_path / "demo" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text(
        "---\nname: demo\ndescription: Use when testing\nversion: 2\nglobal: yes-please\n---\n",
        encoding="utf-8",
    )

    problems = skill_metadata.check_skill(skill, schema)

    assert any("'version' is not in the schema" in problem for problem in problems)
    assert any("'global' must be a boolean" in problem for problem in problems)


def test_open_skill_schema_is_rejected(tmp_path: Path) -> None:
    schema = tmp_path / "schema.json"
    schema.write_text('{"type": "object", "properties": {}, "required": []}', encoding="utf-8")

    with pytest.raises(skill_metadata.SchemaError, match="closed object"):
        skill_metadata.load_schema(schema)


def _guide(tmp_path: Path, command: str) -> tuple[Path, Path]:
    guide = tmp_path / "README.md"
    guide.write_text(
        f"## Quick Install\n\n```bash\ngit clone x\ncd x\n{command}  # note\n```\n",
        encoding="utf-8",
    )
    installer = tmp_path / "sandbox.sh"
    installer.write_text('step setup 0 "tool setup $options"\n', encoding="utf-8")
    return guide, installer


def test_documented_install_commands_are_replayed(tmp_path: Path) -> None:
    guide, installer = _guide(tmp_path, "tool setup")

    assert install_guidance.problems(guide, installer, "Quick Install") == []


def test_undocumented_replay_drift_names_the_line(tmp_path: Path) -> None:
    guide, installer = _guide(tmp_path, "tool setup --all")

    problems = install_guidance.problems(guide, installer, "Quick Install")

    assert problems == [
        f"{guide}:6: PARITY.install-guidance: documented command 'tool setup --all' is not run "
        f"by {installer}; add a step that runs it or correct the documented command"
    ]


def test_repository_install_guidance_matches_the_sandbox() -> None:
    root = Path(__file__).parents[1]
    guide, installer = root / "README.md", root / "scripts/fresh-install/sandbox.sh"

    assert len(install_guidance.documented_commands(guide, "Quick Install")) == 4
    assert install_guidance.problems(guide, installer, "Quick Install") == []


def _hook(tmp_path: Path, text: str) -> Path:
    hook = tmp_path / "pre-commit"
    hook.write_text(text, encoding="utf-8")
    hook.chmod(0o755)
    return hook


def test_generated_pre_commit_hook_is_accepted(tmp_path: Path) -> None:
    python = _hook(tmp_path, "")
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    hook = _hook(
        hooks,
        f"{hook_installed._MARKER}\nINSTALL_PYTHON={python}\n"
        "ARGS=(hook-impl --config=.pre-commit-config.yaml --hook-type=pre-commit)\n",
    )

    assert hook_installed.hook_problems(hook, ".pre-commit-config.yaml", "pre-commit") == []


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        (None, "pre-commit hook is not installed"),
        ("#!/bin/sh\nexit 0\n", "hook does not run pre-commit with .pre-commit-config.yaml"),
    ],
    ids=["missing", "foreign-hook"],
)
def test_missing_or_foreign_hook_is_rejected(
    tmp_path: Path, text: str | None, problem: str
) -> None:
    hook = tmp_path / "pre-commit" if text is None else _hook(tmp_path, text)

    problems = hook_installed.hook_problems(hook, ".pre-commit-config.yaml", "pre-commit")

    assert any(problem in item for item in problems)

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

pytestmark = pytest.mark.boundary

REPO_ROOT = Path(__file__).parents[1]
SCRIPTS = REPO_ROOT / "scripts"
PROOF_FIXTURES = REPO_ROOT / "tests/fixtures/proofs"


def _run(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / script), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def _write_module(root: Path, relpath: str, body: str) -> Path:
    module = root / "repo" / relpath
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(body, encoding="utf-8")
    return module


def _load_settings_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "python_quality_settings", SCRIPTS / "lib/python_quality_settings.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def settings_module() -> ModuleType:
    return _load_settings_module()


def _repository_table() -> dict[str, Any]:
    with (REPO_ROOT / "proofs.toml").open("rb") as handle:
        table: dict[str, Any] = tomllib.load(handle)["python_quality"]
    return table


@pytest.fixture
def table() -> dict[str, Any]:
    return _repository_table()


def test_repository_settings_parse_with_every_key(settings_module: ModuleType) -> None:
    settings = settings_module.python_quality()

    assert "utils" in settings.generic_name_parts
    assert settings.max_loop_depth == 2
    assert settings.fanout_max == 10


def test_settings_reject_a_missing_key(settings_module: ModuleType, table: dict[str, Any]) -> None:
    del table["class_max_lines"]

    with pytest.raises(settings_module.SettingsError, match="class_max_lines is missing"):
        settings_module.parse_python_quality(table)


def test_settings_reject_an_unknown_key(settings_module: ModuleType, table: dict[str, Any]) -> None:
    table["max_public_methods"] = 20

    with pytest.raises(settings_module.SettingsError, match="unknown keys max_public_methods"):
        settings_module.parse_python_quality(table)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("fanout_max", 0),
        ("class_max_init_args", True),
        ("maintained_roots", "src"),
        ("subprocess_wrappers", {"src/x.py": []}),
        ("excluded_paths", {"tests/fixtures": ""}),
    ],
)
def test_settings_reject_a_mistyped_value(
    settings_module: ModuleType, table: dict[str, Any], key: str, value: object
) -> None:
    table[key] = value

    with pytest.raises(settings_module.SettingsError, match=key):
        settings_module.parse_python_quality(table)


@pytest.mark.parametrize(
    "script",
    ["check-python-scalability.py", "check-python-quality.py", "check-python-fanout.py"],
)
def test_unknown_path_filter_fails_with_diagnostic(script: str) -> None:
    result = _run(script, "does/not/exist")

    assert result.returncode == 2
    assert "does/not/exist" in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("script", "fixture", "rule"),
    [
        ("check-python-quality.py", "python.quality/PYQ104/violating", "PYQ104"),
        ("check-python-scalability.py", "python.scalability/PYS250/violating", "PYS250"),
        ("check-python-fanout.py", "python.fanout/FANOUT.general/violating", "FANOUT.general"),
    ],
)
def test_an_excluded_fixture_passed_explicitly_is_still_judged(
    script: str, fixture: str, rule: str
) -> None:
    result = _run(script, str(PROOF_FIXTURES / fixture))

    assert result.returncode == 1
    assert f": {rule}: " in result.stdout + result.stderr


def test_quality_diagnostic_names_file_line_rule_problem_and_fix(tmp_path: Path) -> None:
    module = _write_module(tmp_path, "src/pkg/misc.py", "VALUE = 1\n")

    result = _run("check-python-quality.py", str(module))

    assert result.returncode == 1
    assert (
        "src/pkg/misc.py:1: PYQ104: generic catch-all module name 'misc' is not allowed; "
        "rename the module after what it owns" in result.stderr
    )


@pytest.mark.parametrize("word", ["compat", "fallback", "legacy", "manager", "new", "old", "utils"])
def test_every_house_style_banned_word_fails_as_module_and_directory(
    tmp_path: Path, word: str
) -> None:
    module = _write_module(tmp_path, f"src/pkg/{word}.py", "VALUE = 1\n")
    package = _write_module(tmp_path, f"src/{word}/mod.py", "VALUE = 1\n")

    result = _run("check-python-quality.py", str(module), str(package))

    assert result.returncode == 1
    assert f"PYQ104: generic catch-all module name '{word}'" in result.stderr
    assert f"PYQ103: generic catch-all directory name '{word}'" in result.stderr


def test_compound_names_holding_a_banned_word_stay_valid(tmp_path: Path) -> None:
    module = _write_module(tmp_path, "src/snapshot_compat/common_types.py", "VALUE = 1\n")

    result = _run("check-python-quality.py", str(module))

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("name", "expected"),
    [("render-measures.py", 0), ("unlisted-tool.py", 1), ("snake_tool.py", 1)],
)
def test_command_scripts_need_kebab_case_and_an_approved_name(
    tmp_path: Path, name: str, expected: int
) -> None:
    module = _write_module(tmp_path, f"scripts/{name}", "VALUE = 1\n")

    result = _run("check-python-quality.py", str(module))

    assert result.returncode == expected, result.stderr
    if expected:
        assert "PYQ101" in result.stderr


def test_an_analyzer_class_fails_while_no_base_is_approved(tmp_path: Path) -> None:
    analyzer = _write_module(tmp_path, "src/pkg/scan.py", "class ScanAnalyzer:\n    x = 1\n")
    plain = _write_module(tmp_path, "src/pkg/plain.py", "class Scanner:\n    x = 1\n")

    flagged = _run("check-python-quality.py", "--check", "class-interface", str(analyzer))
    accepted = _run("check-python-quality.py", "--check", "class-interface", str(plain))

    assert flagged.returncode == 1
    assert "PYQ210: analyzer class 'ScanAnalyzer' must inherit an approved base" in flagged.stderr
    assert accepted.returncode == 0, accepted.stderr


def test_test_modules_get_no_subprocess_exemption(tmp_path: Path) -> None:
    module = _write_module(
        tmp_path,
        "tests/test_runner.py",
        "import subprocess\n\n\ndef test_runs() -> None:\n    subprocess.run(['true'])\n",
    )

    result = _run("check-python-quality.py", "--check", "class-interface", str(module))

    assert result.returncode == 1
    assert "tests/test_runner.py:5: PYQ220: subprocess.run in test_runs" in result.stderr


@pytest.mark.parametrize(
    "body",
    [
        "from subprocess import run\n\n\ndef helper() -> None:\n    run(['true'])\n",
        "import subprocess as sp\n\n\ndef helper() -> None:\n    sp.run(['true'])\n",
        "import subprocess\n\n\ndef helper() -> bytes:\n"
        "    return subprocess.check_output(['true'])\n",
        "from subprocess import check_call as cc\n\n\ndef helper() -> None:\n    cc(['true'])\n",
        "import subprocess\n\n\ndef helper() -> int:\n    return subprocess.call(['true'])\n",
    ],
)
def test_quality_flags_aliased_and_alternative_subprocess_calls(tmp_path: Path, body: str) -> None:
    module = _write_module(tmp_path, "src/pkg/scan.py", body)

    result = _run("check-python-quality.py", "--check", "class-interface", str(module))

    assert result.returncode == 1
    assert "PYQ220" in result.stderr


def _scalability(tmp_path: Path, relpath: str, body: str) -> subprocess.CompletedProcess[str]:
    module = _write_module(tmp_path, relpath, body)
    _write_module(tmp_path, "CODEOWNERS", "* @owner\n")
    return _run("check-python-scalability.py", str(module))


@pytest.mark.parametrize(
    "call",
    [
        'Path(".").iterdir()',
        'Path(".").glob("*")',
        'Path(".").rglob("*")',
        'Path(".").exists()',
        'Path(".").stat()',
        'Path(".").is_file()',
        'Path(".").is_dir()',
        'Path(".").mkdir()',
        'Path(".").unlink()',
        'Path(".").open()',
        'Path(".").chmod(0)',
        'Path(".").readlink()',
        '(Path(".") / "x").read_text()',
        'Path.cwd().samefile(".")',
        'os.listdir(".")',
        'os.scandir(".")',
        'os.stat(".")',
        'os.access(".", 0)',
        'os.path.exists(".")',
        'os.path.getmtime(".")',
        'glob.glob("*")',
        'io.open("x")',
        'shutil.rmtree("x")',
        'open("x")',
    ],
)
def test_scalability_flags_sync_filesystem_calls_in_analyzer_code(
    tmp_path: Path, call: str
) -> None:
    result = _scalability(
        tmp_path,
        "src/pkg/scan.py",
        "import glob\nimport io\nimport os\nimport shutil\nfrom pathlib import Path\n\n\n"
        f"def scan() -> object:\n    return {call}\n",
    )

    assert result.returncode == 1
    assert "PYS251: synchronous filesystem I/O in analyzer-like code in scan" in result.stdout


@pytest.mark.parametrize(
    "call",
    ['"a-b".replace("-", "_")', '{"k": 1}.get("k")', "text.open", "handle.exists()"],
)
def test_scalability_ignores_calls_on_values_not_known_to_be_paths(
    tmp_path: Path, call: str
) -> None:
    result = _scalability(
        tmp_path,
        "src/pkg/scan.py",
        f"def scan(text: str, handle: object) -> object:\n    return {call}\n",
    )

    assert result.returncode == 0, result.stdout


def test_every_maintained_root_is_analyzer_like(tmp_path: Path) -> None:
    result = _scalability(
        tmp_path,
        "tests/test_scan.py",
        "from pathlib import Path\n\n\ndef test_scan() -> None:\n    assert Path('.').exists()\n",
    )

    assert result.returncode == 1
    assert "tests/test_scan.py:5: PYS251" in result.stdout


def test_listed_seam_functions_may_touch_the_environment_and_subprocesses(
    tmp_path: Path,
) -> None:
    environment = _scalability(
        tmp_path,
        "src/agent_introspection/cli.py",
        "import os\n\n\ndef _configured_path() -> str | None:\n"
        "    return os.environ.get('X')\n\n\ndef other() -> str | None:\n"
        "    return os.environ.get('X')\n",
    )
    process = _scalability(
        tmp_path,
        "src/agent_introspection/facts.py",
        "import subprocess\n\n\ndef _run_local_command() -> None:\n    subprocess.run(['true'])\n",
    )

    assert environment.returncode == 1
    assert "cli.py:9: PYS252: direct environment read in analyzer-like code in other" in (
        environment.stdout
    )
    assert "_configured_path" not in environment.stdout
    assert process.returncode == 0, process.stdout


@pytest.mark.parametrize(
    "body",
    [
        "from subprocess import run\n\n\ndef scan() -> None:\n    run(['true'])\n",
        "import subprocess as sp\n\n\ndef scan() -> None:\n    sp.run(['true'])\n",
        "import subprocess\n\n\ndef scan() -> bytes:\n"
        "    return subprocess.check_output(['true'])\n",
    ],
)
def test_scalability_flags_aliased_and_alternative_subprocess_calls(
    tmp_path: Path, body: str
) -> None:
    result = _scalability(tmp_path, "src/pkg/scan.py", body)

    assert result.returncode == 1
    assert "PYS252: direct subprocess execution" in result.stdout


def test_analyzer_like_code_needs_an_ownership_marker(tmp_path: Path) -> None:
    module = _write_module(tmp_path, "src/pkg/scan.py", "VALUE = 1\n")

    result = _run("check-python-scalability.py", str(module))

    assert result.returncode == 1
    assert "src/pkg/scan.py:1: PYS253: analyzer-like code has no ownership marker" in result.stdout


@pytest.mark.parametrize(("depth", "expected"), [(2, 0), (3, 1)])
def test_loop_depth_limit_comes_from_settings(tmp_path: Path, depth: int, expected: int) -> None:
    loops = "".join(f"{'    ' * (level + 1)}for i{level} in x:\n" for level in range(depth))
    body = f"def scan(x: list[int]) -> None:\n{loops}{'    ' * (depth + 1)}print(x)\n"

    result = _scalability(tmp_path, "src/pkg/loops.py", body)

    assert result.returncode == expected, result.stdout
    if expected:
        assert "PYS250: nested loop depth 3 exceeds 2 in scan" in result.stdout


@pytest.mark.parametrize(("imports", "expected"), [(10, 0), (11, 1)])
def test_fanout_limit_comes_from_settings(tmp_path: Path, imports: int, expected: int) -> None:
    body = "".join(f"import thirdparty_{index}\n" for index in range(imports))
    module = _write_module(tmp_path, "src/pkg/wide.py", body)

    result = _run("check-python-fanout.py", str(module))

    assert result.returncode == expected, result.stderr
    if expected:
        assert f"src/pkg/wide.py:1: FANOUT.general: architectural imports {imports} > 10" in (
            result.stderr
        )


def test_fanout_reports_an_unparsable_module_instead_of_crashing(tmp_path: Path) -> None:
    module = _write_module(tmp_path, "src/pkg/broken.py", "def broken(:\n")

    result = _run("check-python-fanout.py", str(module))

    assert result.returncode == 1
    assert "src/pkg/broken.py:1: FANOUT.general: could not parse Python file" in result.stderr
    assert "Traceback" not in result.stderr

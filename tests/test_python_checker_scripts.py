from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[1]
SCRIPTS = REPO_ROOT / "scripts"


def _run(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def _fixture(tmp_path: Path, body: str) -> Path:
    module = tmp_path / "repo" / "src" / "pkg" / "scan.py"
    module.parent.mkdir(parents=True)
    module.write_text(body, encoding="utf-8")
    return module


@pytest.mark.parametrize(
    "script",
    [
        ["check-python-scalability.py"],
        ["check-python-quality.py"],
        ["check-python-fanout.py", "--root-package", "agent_introspection", "--source-root", "src"],
    ],
)
def test_unknown_path_filter_fails_with_diagnostic(script: list[str]) -> None:
    result = _run(*script, "does/not/exist")

    assert result.returncode != 0
    assert "does/not/exist" in result.stdout + result.stderr


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
        'os.listdir(".")',
        'os.scandir(".")',
        'os.stat(".")',
        'os.path.exists(".")',
    ],
)
def test_scalability_flags_sync_filesystem_calls_in_analyzer_code(
    tmp_path: Path, call: str
) -> None:
    module = _fixture(
        tmp_path,
        "import os\nfrom pathlib import Path\n\n\n"
        f"class ExampleAnalyzer:\n    def scan(self) -> object:\n        return {call}\n",
    )

    result = _run("check-python-scalability.py", "--fixture", str(module))

    assert result.returncode == 1
    assert "PYS251" in result.stdout


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
    module = _fixture(tmp_path, body)

    result = _run("check-python-quality.py", "--check", "class-interface", "--fixture", str(module))

    assert result.returncode == 1
    assert "PYQ220" in result.stderr


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
    module = _fixture(tmp_path, body + "\n\nclass ScanAnalyzer:\n    pass\n")

    result = _run("check-python-scalability.py", "--fixture", str(module))

    assert result.returncode == 1
    assert "PYS252" in result.stdout

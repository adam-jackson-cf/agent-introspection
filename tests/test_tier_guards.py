from __future__ import annotations

import os
import pwd
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest

from tests.support.script_loader import CANARIES, REPO_ROOT, SCRIPTS, load_script

pytestmark = pytest.mark.boundary

GUARD_CASES = CANARIES / "tests.t1-guard"
USER_DATA_CASES = CANARIES / "tests.user-data"
REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def _pytest(fixture: Path, report: Path) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    completed = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            str(fixture),
            f"--junitxml={report}",
        ],
        REPO_ROOT,
    )
    outcomes = [
        next((child.tag for child in case if child.tag in {"failure", "error"}), "passed")
        for case in ElementTree.parse(report).iter("testcase")
    ]
    return completed, outcomes


@pytest.mark.parametrize(
    ("fixture", "verdict"),
    [
        ("violating/test_starts_process.py", "finding"),
        ("cases/test_opens_network.py", "finding"),
        ("cases/test_teardown_process.py", "finding"),
        ("cases/test_swallowed_violation.py", "finding"),
        ("cases/test_boundary_process.py", "clean"),
        ("passing/test_in_memory_total.py", "clean"),
    ],
)
def test_the_t1_guard_fails_process_and_network_use_outside_boundary_tests(
    tmp_path: Path, fixture: str, verdict: str
) -> None:
    completed, outcomes = _pytest(GUARD_CASES / fixture, tmp_path / "report.xml")
    if verdict == "finding":
        assert completed.returncode == 1
        assert "TEST.t1-isolation" in completed.stdout
        assert "failure" in outcomes or "error" in outcomes
    else:
        assert completed.returncode == 0, completed.stdout
        assert outcomes
        assert set(outcomes) == {"passed"}


def test_the_user_data_guard_blocks_a_real_home_write_and_fails_the_test(tmp_path: Path) -> None:
    target = REAL_HOME / "agent-introspection-user-data-canary.txt"
    completed, outcomes = _pytest(
        USER_DATA_CASES / "violating" / "test_writes_real_home.py", tmp_path / "report.xml"
    )
    assert completed.returncode == 1
    assert "TEST.user-data-guard" in completed.stdout
    assert outcomes == ["failure"]
    assert not target.exists()


def test_writes_under_tmp_path_and_the_redirected_home_pass(tmp_path: Path) -> None:
    completed, outcomes = _pytest(
        USER_DATA_CASES / "passing" / "test_writes_redirected_home.py", tmp_path / "report.xml"
    )
    assert completed.returncode == 0, completed.stdout
    assert outcomes == ["passed"]


@pytest.mark.parametrize(
    ("fixture", "returncode"),
    [("violating/spawns.test.ts", 1), ("passing/in-memory.test.ts", 0)],
)
def test_the_bun_t1_guard_fails_a_process_started_by_a_dashboard_test(
    fixture: str, returncode: int
) -> None:
    bun = shutil.which("bun")
    assert bun is not None, "bun must be on PATH for the dashboard tier"
    completed = _run([bun, "test", str(GUARD_CASES / fixture)], REPO_ROOT / "dashboard")
    assert completed.returncode == returncode, completed.stdout + completed.stderr
    assert ("TEST.t1-isolation" in completed.stdout + completed.stderr) == bool(returncode)


def test_a_tier_selector_lists_exactly_the_tests_it_selects() -> None:
    checker = load_script("check-test-tiers")
    passing = (CANARIES / "tests.tiers" / "passing").relative_to(REPO_ROOT).as_posix()
    t1, failure = checker.collect("not boundary and not deferred", [passing])
    assert failure is None
    assert t1 == [
        f"{passing}/test_in_memory_sum.py::test_the_sum_of_two_numbers_is_computed_in_memory"
    ]
    t2, failure = checker.collect(
        "(boundary or deferred) and not (boundary and deferred)", [passing]
    )
    assert failure is None
    assert t2 == [f"{passing}/test_process_exit.py::test_a_real_process_exits_cleanly"]
    journey, failure = checker.collect("boundary and deferred", [passing])
    assert failure is None
    assert journey == [f"{passing}/test_lever_journey.py::test_the_journey_tier_holds_a_journey"]


def _git(repo: Path, *args: str) -> None:
    completed = _run(["git", "-c", "user.name=t", "-c", "user.email=t@localhost", *args], repo)
    assert completed.returncode == 0, completed.stderr


def _scenario(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "proofs.toml", repo / "proofs.toml")
    (repo / "proofs.toml").write_text(
        (repo / "proofs.toml").read_text().replace('base_ref = "origin/main"', 'base_ref = "main"')
    )
    (repo / "tests" / "test_spool_batches.py").write_text(
        "import subprocess\n\n\ndef test_a_batch_runs() -> None:\n"
        "    subprocess.run(['true'], check=False, timeout=5)\n"
    )
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def test_a_raised_test_timeout_is_reported_against_the_base(tmp_path: Path) -> None:
    repo = _scenario(tmp_path)
    test = repo / "tests" / "test_spool_batches.py"
    test.write_text(test.read_text().replace("timeout=5", "timeout=50"))
    _git(repo, "add", "-A")
    for event in ("--staged", "--merge-base"):
        completed = _run([sys.executable, str(SCRIPTS / "check-test-evasion.py"), event], repo)
        assert completed.returncode == 1
        assert "raises its timeout from 5 to 50" in completed.stderr


def test_an_unchanged_timeout_is_clean_against_the_base(tmp_path: Path) -> None:
    repo = _scenario(tmp_path)
    completed = _run([sys.executable, str(SCRIPTS / "check-test-evasion.py"), "--staged"], repo)
    assert completed.returncode == 0, completed.stderr


def test_naming_and_placement_judge_only_added_paths_in_the_event(tmp_path: Path) -> None:
    repo = _scenario(tmp_path)
    (repo / "tests" / "spool_cases.py").write_text("CASES = []\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "misplaced file already on the base")
    (repo / "tests" / "test_spool_batches_v2.py").write_text("def test_x() -> None:\n    pass\n")
    _git(repo, "add", "-A")
    completed = _run([sys.executable, str(SCRIPTS / "check-test-placement.py"), "--staged"], repo)
    assert completed.returncode == 1
    assert "tests/test_spool_batches_v2.py: NAME.test-files" in completed.stderr
    assert "spool_cases.py" not in completed.stderr

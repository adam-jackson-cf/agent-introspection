from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.boundary

_REPO = Path(__file__).parents[1]
_SCRIPTS = _REPO / "scripts"
_PROOFS = _REPO / "tests" / "fixtures" / "proofs"
_CONFORMANCE = _PROOFS / "adapter-conformance"
_SCENARIOS = [
    ("test_growth", "tests.growth", "passing", ""),
    ("test_growth", "tests.growth", "violating-name", "TEST.growth.justified"),
    ("test_growth", "tests.growth", "violating-duplicate", "TEST.duplicate-path"),
    ("coverage_ratchet", "tests.coverage-ratchet", "passing", ""),
    ("coverage_ratchet", "tests.coverage-ratchet", "violating-pragma", "TEST.coverage-ratchet"),
    ("coverage_ratchet", "tests.coverage-ratchet", "violating-floor", "TEST.coverage-ratchet"),
    ("identity_artefacts", "tests.identity-artefacts", "passing", ""),
    ("identity_artefacts", "tests.identity-artefacts", "violating", "TEST.identity-artefacts"),
    ("golden_changes", "tests.golden-change-record", "passing", ""),
    ("golden_changes", "tests.golden-change-record", "violating", "TEST.golden-change-record"),
    ("test_budget", "tests.budget", "passing", ""),
    ("test_budget", "tests.budget", "violating-wall", "TEST.budget.t1-time"),
    ("test_budget", "tests.budget", "violating-per-test", "TEST.budget.per-test-time"),
    ("verification_surface", "surface.feature-map", "passing", ""),
    ("verification_surface", "surface.feature-map", "violating-map", "SURFACE.feature-map"),
    ("verification_surface", "surface.feature-map", "violating-journey", "SURFACE.journeys"),
    ("lever_isolation", "surface.lever", "passing", ""),
    ("lever_isolation", "surface.lever", "violating", "SURFACE.lever-isolation"),
]


def _lib(name: str) -> ModuleType:
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    __import__(f"lib.{name}")
    return sys.modules[f"lib.{name}"]


def _environment() -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.pop("PROOFS_BASE_REF", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def _gate(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/run-ci-quality-gates.sh", "check", *args],
        cwd=_REPO,
        env=_environment(),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.mark.parametrize(("check", "claim", "fixture", "rule"), _SCENARIOS)
def test_scenario_canary_passes_or_names_its_rule(
    check: str, claim: str, fixture: str, rule: str
) -> None:
    completed = _gate("scenario", check, str(_PROOFS / claim / fixture))

    if rule:
        assert completed.returncode != 0
        assert f": {rule}: " in completed.stdout + completed.stderr
    else:
        assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.parametrize(("fixture", "failing"), [("passing", False), ("violating", True)])
def test_runner_canary_proves_failure_propagation(fixture: str, failing: bool) -> None:
    runner = _PROOFS / "tests.failure-propagation" / fixture / "scripts" / "run-ci-quality-gates.sh"

    completed = _gate("failure_propagation", str(runner))

    assert (completed.returncode != 0) is failing
    assert ("TEST.failure-propagation" in completed.stdout + completed.stderr) is failing


@pytest.mark.parametrize(("fixture", "failing"), [("passing", False), ("violating", True)])
def test_run_report_canary_proves_failure_propagation(fixture: str, failing: bool) -> None:
    completed = _gate(
        "test_run", str(_PROOFS / "tests.failure-propagation" / fixture / "t1-pytest.json")
    )

    assert (completed.returncode != 0) is failing


def _run_fixture(case_id: str, tmp_path: Path) -> dict[str, tuple[str, str]]:
    adapter = _lib("pytest_adapter")
    junit = tmp_path / "report.xml"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-q",
            "-o",
            "junit_family=xunit1",
            f"--junitxml={junit}",
            "tests",
        ],
        cwd=_CONFORMANCE / case_id,
        env=_environment(),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return {case.id: (case.outcome, case.reason) for case in adapter.report(junit, "t1").cases}


def test_conformance_runtime_skip_is_recorded(tmp_path: Path) -> None:
    records = _run_fixture("runtime-skip-recorded", tmp_path)

    assert records == {
        "tests/test_vendor_sync.py::test_vendor_sync_reads_the_feed": (
            "skipped",
            "waiting on vendor fix",
        )
    }


def test_conformance_expected_failure_is_recorded(tmp_path: Path) -> None:
    records = _run_fixture("expected-failure-recorded", tmp_path)

    assert records == {
        "tests/test_vendor_sync.py::test_vendor_sync_reads_the_feed": ("xfail", "known defect")
    }


def test_conformance_tier_selection_lists_exactly_its_tests() -> None:
    adapter = _lib("pytest_adapter")

    listing = adapter.collect(_CONFORMANCE / "tier-selection", "t1", ["-m", "not boundary"], 120)

    assert list(listing.ids) == ["tests/test_case.py::test_fast_total"]


def test_conformance_empty_collection_is_a_finding() -> None:
    adapter = _lib("pytest_adapter")
    evidence = _lib("run_evidence")

    with pytest.raises(evidence.EvidenceError, match="collected no tests"):
        adapter.collect(_CONFORMANCE / "empty-collection", "t1", ["-m", "not boundary"], 120)


def test_bun_static_cases_report_titles_and_unparsable_files(tmp_path: Path) -> None:
    adapter = _lib("bun_adapter")
    (tmp_path / "a.test.ts").write_text(
        'describe("registry", () => {\n'
        '  test("answers with the harness list", () => { expect(1).toBe(1); });\n'
        "});\n",
        encoding="utf-8",
    )
    (tmp_path / "b.test.ts").write_text('test("broken", () => {\n', encoding="utf-8")

    cases, unparsable = adapter.static_cases(tmp_path, ["a.test.ts", "b.test.ts"])

    assert [case.id for case in cases] == ["a.test.ts::registry > answers with the harness list"]
    assert list(unparsable) == ["b.test.ts"]


def test_machine_id_names_platform_cpu_and_runtimes() -> None:
    probe = _lib("machine_probe")

    machine = probe.machine_id()

    assert machine.startswith(f"{sys.platform}-")
    assert "-python" in machine
    assert "-bun" in machine

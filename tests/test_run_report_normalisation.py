from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPTS = Path(__file__).parents[1] / "scripts"


def _lib(name: str) -> ModuleType:
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    __import__(f"lib.{name}")
    return sys.modules[f"lib.{name}"]


_PYTEST_JUNIT = """<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest tests">
<testsuite name="pytest" errors="0" failures="1" skipped="2" tests="5" time="1.250"
 timestamp="2026-11-27T17:20:12.368400+00:00">
<testcase classname="tests.test_a" name="test_adds_two_amounts"
 file="tests/test_a.py" line="2" time="0.010"/>
<testcase classname="tests.test_a" name="test_vendor_feed"
 file="tests/test_a.py" line="5" time="0.001">
<skipped type="pytest.skip" message="waiting on vendor fix">
tests/test_a.py:7: waiting on vendor fix</skipped>
</testcase>
<testcase classname="tests.test_a" name="test_known_defect"
 file="tests/test_a.py" line="8" time="0.002">
<skipped type="pytest.xfail" message="known defect"/></testcase>
<testcase classname="tests.test_a.TestTotals" name="test_rows[1]"
 file="tests/test_a.py" line="12" time="0.020">
<failure message="assert 1 == 2">trace</failure></testcase>
<testcase classname="tests.test_a" name="test_slow_call"
 file="tests/test_a.py" line="20" time="2.000">
<failure message="Failed: Timeout &gt;2.0s">trace</failure></testcase>
</testsuite></testsuites>
"""

_BUN_JUNIT = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites name="bun test" tests="3" failures="0" skipped="1" time="0.25">
  <testsuite name="server.test.ts" file="server.test.ts" tests="3">
    <testsuite name="registry" file="server.test.ts" line="2" tests="2">
      <testcase name="answers with the harness list" classname="registry" time="0.004"
       file="server.test.ts"/>
      <testcase name="needs a live ClickHouse" classname="registry" time="0" file="server.test.ts">
        <skipped />
      </testcase>
    </testsuite>
    <testcase name="rejects a foreign host" classname="" time="0.001" file="server.test.ts"/>
  </testsuite>
</testsuites>
"""


def _pytest_records(tmp_path: Path) -> dict[str, tuple[str, str]]:
    adapter = _lib("pytest_adapter")
    junit = tmp_path / "t1.xml"
    junit.write_text(_PYTEST_JUNIT, encoding="utf-8")
    report = adapter.report(junit, "t1")
    return {case.id: (case.outcome, case.reason) for case in report.cases}


def test_pytest_report_keeps_runtime_skip_outcome_and_reason(tmp_path: Path) -> None:
    records = _pytest_records(tmp_path)

    assert records["tests/test_a.py::test_vendor_feed"] == ("skipped", "waiting on vendor fix")


def test_pytest_report_normalises_expected_failures_as_xfail(tmp_path: Path) -> None:
    records = _pytest_records(tmp_path)

    assert records["tests/test_a.py::test_known_defect"] == ("xfail", "known defect")


def test_pytest_report_ids_name_file_class_and_parameter_row(tmp_path: Path) -> None:
    records = _pytest_records(tmp_path)

    assert records["tests/test_a.py::TestTotals::test_rows[1]"][0] == "failed"
    assert records["tests/test_a.py::test_adds_two_amounts"] == ("passed", "")


def test_pytest_report_marks_hang_timeouts(tmp_path: Path) -> None:
    records = _pytest_records(tmp_path)

    assert records["tests/test_a.py::test_slow_call"][0] == "timeout"


def test_pytest_report_records_session_wall_and_start(tmp_path: Path) -> None:
    adapter = _lib("pytest_adapter")
    junit = tmp_path / "t1.xml"
    junit.write_text(_PYTEST_JUNIT, encoding="utf-8")

    report = adapter.report(junit, "t1")

    assert report.wall_seconds == pytest.approx(1.25)
    assert report.started_at == pytest.approx(1795800012.3684)


def test_bun_report_ids_join_the_describe_chain(tmp_path: Path) -> None:
    adapter = _lib("bun_adapter")
    junit = tmp_path / "bun.xml"
    junit.write_text(_BUN_JUNIT, encoding="utf-8")

    report = adapter.report(junit, "t1", "dashboard/")

    outcomes = {case.id: case.outcome for case in report.cases}
    assert outcomes == {
        "dashboard/server.test.ts::registry > answers with the harness list": "passed",
        "dashboard/server.test.ts::registry > needs a live ClickHouse": "skipped",
        "dashboard/server.test.ts::rejects a foreign host": "passed",
    }


def test_unreadable_junit_is_an_evidence_error(tmp_path: Path) -> None:
    adapter = _lib("pytest_adapter")
    evidence = _lib("run_evidence")
    junit = tmp_path / "t1.xml"
    junit.write_text("<testsuites", encoding="utf-8")

    with pytest.raises(evidence.EvidenceError, match="unreadable"):
        adapter.report(junit, "t1")


@pytest.mark.parametrize(
    ("seconds", "message"),
    [(-1, "nonnegative"), (float("inf"), "finite"), ("slow", "not a number")],
)
def test_normalised_report_rejects_invalid_durations(
    tmp_path: Path, seconds: object, message: str
) -> None:
    evidence = _lib("run_evidence")
    path = tmp_path / "t1.json"
    case = {"id": "tests/test_a.py::test_x", "outcome": "passed", "seconds": seconds}
    payload = {"schema": 1, "tier": "t1", "framework": "pytest", "started_at": 1, "wall_seconds": 1}
    path.write_text(json.dumps({**payload, "cases": [case]}), encoding="utf-8")

    with pytest.raises(evidence.EvidenceError, match=message):
        evidence.read_report(path)


def test_normalised_report_round_trips(tmp_path: Path) -> None:
    evidence = _lib("run_evidence")
    record = evidence.CaseRecord("tests/test_a.py::test_x", "passed", "", 0.5, 0)
    report = evidence.RunReport("t1", "pytest", 10.0, 2.0, (record,))
    path = tmp_path / "t1.json"

    evidence.write_report(report, path)

    assert evidence.read_report(path) == report


def test_collection_output_lists_stable_ids() -> None:
    adapter = _lib("pytest_adapter")
    output = (
        "tests/test_a.py::test_x\ntests/test_a.py::TestK::test_m[1]\n\n2 tests collected in 0.01s\n"
    )

    assert adapter.parse_collection(output) == [
        "tests/test_a.py::test_x",
        "tests/test_a.py::TestK::test_m[1]",
    ]


@pytest.mark.parametrize(
    ("ids", "message"), [([], "collected no tests"), (["a::b", "a::b"], "duplicate")]
)
def test_tier_listing_rejects_empty_or_duplicate_collections(ids: list[str], message: str) -> None:
    evidence = _lib("run_evidence")

    with pytest.raises(evidence.EvidenceError, match=message):
        evidence.listing_from_ids("t1", "pytest", ids)

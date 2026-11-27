from __future__ import annotations

import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest

from tests.support.script_loader import CANARIES, load_script

checker = load_script("check-test-evasion")
CASES = CANARIES / "tests.evasion"


def _findings(relative: str) -> list[str]:
    path = CASES / relative
    return [finding.render() for finding in checker.source_findings(str(path))]


@pytest.mark.parametrize(
    ("relative", "problem"),
    [
        ("violating/test_skips_a_case.py", "is marked skip"),
        ("cases/test_reruns_until_green.py", "is marked flaky"),
        ("cases/test_expected_failure.py", "is marked xfail"),
        ("cases/skip.test.ts", "uses .skip"),
    ],
)
def test_an_unapproved_marker_is_reported_with_the_test_id(relative: str, problem: str) -> None:
    (finding,) = _findings(relative)
    assert "TEST.no-evasion" in finding
    assert problem in finding
    assert "::" in finding


def test_a_test_without_markers_is_clean() -> None:
    assert _findings("passing/test_runs_every_case.py") == []


def test_an_approved_exception_names_the_test_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "proofs.toml").write_text(
        "[layout]\n"
        'test_file_patterns = ["tests/test_*.py"]\n'
        'support_dir = "tests/support"\n'
        'fixtures_dir = "tests/fixtures"\n'
        "[[exception]]\n"
        'rule = "TEST.no-evasion"\n'
        'path = "tests/test_skips_a_case.py::test_a_case_that_is_skipped"\n'
        'reason = "vendor fix pending"\n'
        'approved_by = "owner, 2026-11-27"\n',
        encoding="utf-8",
    )
    source = tmp_path / "tests" / "test_skips_a_case.py"
    source.parent.mkdir()
    source.write_text((CASES / "violating" / "test_skips_a_case.py").read_text(), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    remaining, scanned = checker.collect(tmp_path, ["tests/test_skips_a_case.py"])
    assert (remaining, scanned) == ([], 1)
    source.write_text(source.read_text().replace("test_a_case_that_is_skipped", "test_other"))
    (finding,) = checker.collect(tmp_path, ["tests/test_skips_a_case.py"])[0]
    assert finding.test_id == "tests/test_skips_a_case.py::test_other"


@pytest.mark.parametrize(
    ("child", "record"),
    [
        ('<skipped message="waiting on vendor fix" />', ("skipped", "waiting on vendor fix")),
        ('<skipped type="pytest.xfail" message="known defect" />', ("xfail", "known defect")),
        ('<rerunFailure message="flaked" />', ("rerun", "flaked")),
    ],
)
def test_a_run_report_record_keeps_its_outcome_and_reason(
    tmp_path: Path, child: str, record: tuple[str, str]
) -> None:
    report = tmp_path / "t1.xml"
    report.write_text(
        f'<testsuites><testsuite><testcase classname="tests.test_x" name="test_y[1]">{child}'
        "</testcase></testsuite></testsuites>",
        encoding="utf-8",
    )
    (finding,) = checker.report_findings(report)
    assert finding.test_id == "tests/test_x.py::test_y"
    assert record[1] in finding.problem
    (case,) = ElementTree.parse(report).iter("testcase")
    assert checker.normalise(case) == [record]


def test_an_empty_run_report_is_reported(tmp_path: Path) -> None:
    report = tmp_path / "t1.xml"
    report.write_text("<testsuites><testsuite /></testsuites>", encoding="utf-8")
    (finding,) = checker.report_findings(report)
    assert "holds no test cases" in finding.problem


def test_a_passing_run_report_is_clean(tmp_path: Path) -> None:
    report = tmp_path / "t1.xml"
    report.write_text(
        '<testsuites><testsuite><testcase classname="tests.test_x" name="test_y" />'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    assert checker.report_findings(report) == []

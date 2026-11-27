from __future__ import annotations

import json
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from lib.command_runner import run_command
from lib.test_evidence import CaseRecord, EvidenceError, RunReport, StaticCase

FRAMEWORK = "bun"
CASE_LISTER = Path(__file__).resolve().parents[1] / "list-bun-test-cases.ts"
LIST_TIMEOUT_SECONDS = 120


def _outcome(case: ElementTree.Element) -> tuple[str, str]:
    for child in case:
        message = child.get("message", "") or (child.text or "").strip()
        if child.tag == "skipped":
            return "skipped", message
        if child.tag == "error":
            return "error", message
        if child.tag == "failure":
            timed_out = "timed out" in message.lower() or "timeout" in child.get("type", "").lower()
            return ("timeout" if timed_out else "failed", message)
    return "passed", ""


def _cases(suite: ElementTree.Element, prefix: str, groups: tuple[str, ...]) -> list[CaseRecord]:
    records: list[CaseRecord] = []
    for child in suite:
        if child.tag == "testsuite":
            records.extend(_cases(child, prefix, (*groups, child.get("name", ""))))
        elif child.tag == "testcase":
            outcome, reason = _outcome(child)
            name = " > ".join((*groups, child.get("name", "")))
            identifier = f"{prefix}{child.get('file', '')}::{name}"
            records.append(CaseRecord(identifier, outcome, reason, float(child.get("time", "0")), 0))
    return records


def report(junit: Path, tier: str, prefix: str) -> RunReport:
    try:
        root = ElementTree.parse(junit).getroot()
        finished = junit.stat().st_mtime
    except (OSError, ElementTree.ParseError) as error:
        raise EvidenceError(f"bun report {junit} is unreadable: {error}") from error
    if root.tag != "testsuites":
        raise EvidenceError(f"bun report {junit} has no testsuites root")
    cases: list[CaseRecord] = []
    for file_suite in root.findall("testsuite"):
        cases.extend(_cases(file_suite, prefix, ()))
    wall = float(root.get("time", "0"))
    return RunReport(tier, FRAMEWORK, max(finished - wall, 0.0), wall, tuple(cases))


def static_cases(repo: Path, files: list[str]) -> tuple[list[StaticCase], dict[str, str]]:
    if not files:
        return [], {}
    argv = ["bun", str(CASE_LISTER), "--root", str(repo), *files]
    completed = run_command(argv, repo, LIST_TIMEOUT_SECONDS)
    if completed.returncode != 0:
        raise EvidenceError(f"bun test case listing failed: {completed.stderr.strip()}")
    raw = json.loads(completed.stdout)
    cases = [
        StaticCase(item["id"], item["file"], item["line"], item["name"], item["fingerprint"])
        for item in raw["cases"]
    ]
    return cases, {item["file"]: item["error"] for item in raw["unparsable"]}


def behaviour_words(title: str) -> list[str]:
    return [word for word in "".join(c if c.isalnum() else " " for c in title).split() if word]

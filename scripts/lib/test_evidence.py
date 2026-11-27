from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

SCHEMA = 1
OUTCOMES = frozenset({"passed", "failed", "error", "skipped", "xfail", "timeout"})


class EvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class CaseRecord:
    id: str
    outcome: str
    reason: str
    seconds: float
    reruns: int


@dataclass(frozen=True)
class RunReport:
    tier: str
    framework: str
    started_at: float
    wall_seconds: float
    cases: tuple[CaseRecord, ...]


@dataclass(frozen=True)
class TierListing:
    tier: str
    framework: str
    ids: tuple[str, ...]


@dataclass(frozen=True)
class StaticCase:
    id: str
    file: str
    line: int
    name: str
    fingerprint: str


def _seconds(value: object, owner: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise EvidenceError(f"{owner} is not a number")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise EvidenceError(f"{owner} must be a finite, nonnegative number of seconds")
    return number


def _case(raw: object) -> CaseRecord:
    if not isinstance(raw, dict):
        raise EvidenceError("a case is not an object")
    identifier, outcome = raw.get("id"), raw.get("outcome")
    if not isinstance(identifier, str) or not identifier:
        raise EvidenceError("a case has no id")
    if outcome not in OUTCOMES:
        raise EvidenceError(f"{identifier} has unknown outcome {outcome!r}")
    reruns = raw.get("reruns", 0)
    if not isinstance(reruns, int) or reruns < 0:
        raise EvidenceError(f"{identifier} has an invalid rerun count")
    return CaseRecord(
        identifier,
        str(outcome),
        str(raw.get("reason", "")),
        _seconds(raw.get("seconds"), f"{identifier} seconds"),
        reruns,
    )


def report_from_json(raw: dict[str, Any]) -> RunReport:
    if raw.get("schema") != SCHEMA:
        raise EvidenceError(f"run report schema is not {SCHEMA}")
    cases = raw.get("cases")
    if not isinstance(cases, list):
        raise EvidenceError("run report has no case list")
    return RunReport(
        str(raw.get("tier", "")),
        str(raw.get("framework", "")),
        _seconds(raw.get("started_at"), "started_at"),
        _seconds(raw.get("wall_seconds"), "wall_seconds"),
        tuple(_case(case) for case in cases),
    )


def read_report(path: Path) -> RunReport:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise EvidenceError(f"run report is missing or unreadable: {error}") from error
    if not isinstance(raw, dict):
        raise EvidenceError("run report is not an object")
    return report_from_json(raw)


def write_report(report: RunReport, path: Path) -> None:
    payload = {"schema": SCHEMA, **asdict(report)}
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def listing_from_ids(tier: str, framework: str, ids: list[str]) -> TierListing:
    if not ids:
        raise EvidenceError(f"tier {tier} collected no tests")
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    if duplicates:
        raise EvidenceError(f"tier {tier} collected duplicate ids: {', '.join(duplicates)}")
    return TierListing(tier, framework, tuple(ids))


def read_listing(path: Path) -> TierListing:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise EvidenceError(f"tier listing is missing or unreadable: {error}") from error
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA or not isinstance(raw.get("ids"), list):
        raise EvidenceError("tier listing is not a schema 1 listing")
    return listing_from_ids(str(raw.get("tier", "")), str(raw.get("framework", "")), raw["ids"])


def write_listing(listing: TierListing, path: Path) -> None:
    payload = {"schema": SCHEMA, **asdict(listing)}
    path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")

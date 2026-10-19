"""Short, human-readable summaries of the ``doctor`` and ``setup`` JSON reports.

The JSON report stays on stdout for tools; these lines go to stderr for a person (or an
agent) reading the run: each check that is not a pass, the files a run added or changed
with their backups, and what to do next.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

_ORDER = ("fail", "warn", "info", "off")
_PAST = {"add": "added", "change": "changed", "remove": "removed"}
_NEXT = (
    "Use each enabled harness once, then rerun `agent-introspection doctor`: a fresh "
    "install's telemetry warnings clear once each harness has sent data."
)
_RUNNING = (
    "To find running sessions: `pgrep -fl claude` and `pgrep -fl codex` "
    "(and `pgrep -fl omp` when omp is enabled)."
)


def _check_line(item: Mapping[str, Any]) -> list[str]:
    harness = f" ({item['harness']})" if item.get("harness") else ""
    lines = [f"  {item['status']:<4} {item['name']}{harness}: {item['detail']}"]
    if item.get("fix") and item["status"] in ("fail", "warn"):
        lines.append(f"       fix: {item['fix']}")
    return lines


def doctor_lines(report: Mapping[str, Any]) -> list[str]:
    """Return a count line, then every check that is not a pass, the worst first."""
    counts = ", ".join(f"{count} {status}" for status, count in report["summary"].items() if count)
    verdict = "passed" if report["passed"] else "FAILED"
    checks = report.get("checks", report.get("not_passing", []))
    rest = sorted(
        (item for item in checks if item["status"] in _ORDER),
        key=lambda item: _ORDER.index(item["status"]),
    )
    return [f"doctor: {verdict} ({counts})", *(line for item in rest for line in _check_line(item))]


def _harness_file_action(item: Mapping[str, Any]) -> str:
    changed = any(change.get("action") in ("add", "change") for change in item["changes"])
    if not changed:
        return "unchanged"
    return "change" if item["exists"] else "add"


def _files(node: object) -> Iterator[tuple[str, str, str | None]]:
    """Yield (path, action, backup) for every file entry in a setup report."""
    if isinstance(node, Mapping):
        if "path" in node and "action" in node:
            yield str(node["path"]), str(node["action"]), node.get("backup")
        elif "path" in node and "changes" in node:
            yield str(node["path"]), _harness_file_action(node), node.get("backup")
        else:
            for value in node.values():
                yield from _files(value)
    elif isinstance(node, list):
        for value in node:
            yield from _files(value)


def _file_lines(report: Mapping[str, Any], dry_run: bool) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for path, action, backup in _files(report.get("steps", {})):
        if action == "unchanged" or path in seen:
            continue
        seen.add(path)
        if dry_run:
            note = "new file" if action == "add" else "backed up first"
            lines.append(f"  would {action} {path} ({note})")
        else:
            note = f"backup: {backup}" if backup else "new file" if action == "add" else "no backup"
            lines.append(f"  {_PAST.get(action, action)} {path} ({note})")
    return lines


def _next_steps(report: Mapping[str, Any]) -> list[str]:
    harnesses = report.get("steps", {}).get("harnesses", {})
    steps = [
        step
        for result in harnesses.values()
        if isinstance(result, Mapping)
        for step in result.get("next_steps", [])
    ]
    return [*steps, *([_RUNNING] if steps else []), _NEXT]


def setup_lines(report: Mapping[str, Any]) -> list[str]:
    """Return what a setup run did or would do, its doctor result, and what comes next."""
    if not report["passed"] and report.get("stopped_at"):
        return [
            f"setup: stopped at {report['stopped_at']}: {report['error']}",
            "  fix that and rerun setup: every step is idempotent.",
        ]
    dry_run = bool(report.get("dry_run"))
    files = _file_lines(report, dry_run)
    if dry_run:
        return [
            "setup --dry-run: every step can run; nothing was changed.",
            *(["files:", *files] if files else ["files: none would change"]),
            "Rerun without --dry-run to apply.",
        ]
    lines = ["setup: every step ran.", *(["files:", *files] if files else ["files: none changed"])]
    if "doctor" in report:
        lines.extend(doctor_lines(report["doctor"]))
    return [*lines, "next:", *(f"  {step}" for step in _next_steps(report))]

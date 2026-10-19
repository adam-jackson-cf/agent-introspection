from __future__ import annotations

from typing import Any

from agent_introspection import report_summary

DOCTOR: dict[str, Any] = {
    "passed": True,
    "summary": {"pass": 2, "warn": 1, "fail": 0, "off": 1, "info": 0},
    "checks": [
        {"name": "config", "status": "pass", "detail": "loaded", "fix": ""},
        {"name": "prompt_export", "status": "off", "detail": "off", "fix": "x", "harness": "codex"},
        {
            "name": "telemetry",
            "status": "warn",
            "detail": "no rows in 24 h",
            "fix": "use it once",
            "harness": "claude-code",
        },
        {"name": "signoz", "status": "pass", "detail": "ok", "fix": ""},
    ],
}


def test_doctor_lines_count_then_list_what_is_not_a_pass_worst_first() -> None:
    assert report_summary.doctor_lines(DOCTOR) == [
        "doctor: passed (2 pass, 1 warn, 1 off)",
        "  warn telemetry (claude-code): no rows in 24 h",
        "       fix: use it once",
        "  off  prompt_export (codex): off",
    ]


def _setup(dry_run: bool) -> dict[str, Any]:
    return {
        "dry_run": dry_run,
        "passed": True,
        "steps": {
            "config": {"path": "/c.toml", "action": "add", "backup": None, "diff": "+[signoz]\n"},
            "harnesses": {
                "claude-code": {
                    "files": [
                        {
                            "path": "/s.json",
                            "exists": True,
                            "backup": None if dry_run else "/s.json.bak-1",
                            "changes": [{"action": "add"}],
                        }
                    ],
                    "hooks": {
                        "runtime": [{"path": "/shim", "action": "unchanged", "backup": None}],
                        "settings": [{"path": "/s.json", "action": "change", "backup": None}],
                    },
                    "next_steps": ["Restart running Claude Code sessions."],
                }
            },
        },
        **({} if dry_run else {"doctor": {**DOCTOR, "not_passing": DOCTOR["checks"][2:3]}}),
    }


def test_a_dry_run_lists_each_file_it_would_write_and_whether_it_is_backed_up() -> None:
    assert report_summary.setup_lines(_setup(dry_run=True)) == [
        "setup --dry-run: every step can run; nothing was changed.",
        "files:",
        "  would add /c.toml (new file)",
        "  would change /s.json (backed up first)",
        "config diff:",
        "+[signoz]",
        "Rerun without --dry-run to apply.",
    ]


def test_a_run_lists_its_backups_doctor_and_next_steps() -> None:
    lines = report_summary.setup_lines(_setup(dry_run=False))

    assert lines[:4] == [
        "setup: every step ran.",
        "files:",
        "  added /c.toml (new file)",
        "  changed /s.json (backup: /s.json.bak-1)",
    ]
    assert "doctor: passed (2 pass, 1 warn, 1 off)" in lines
    assert lines[lines.index("next:") + 1] == "  Restart running Claude Code sessions."
    assert "pgrep" in lines[-2]


def test_a_stopped_run_names_the_step() -> None:
    report = {"passed": False, "stopped_at": "signoz", "error": "unreachable", "steps": {}}

    assert report_summary.setup_lines(report)[0] == "setup: stopped at signoz: unreachable"

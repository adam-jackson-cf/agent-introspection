from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.boundary, pytest.mark.deferred]

REPO = Path(__file__).parents[2]
LEVER_TIMEOUT_SECONDS = 1500
DOCTOR_MARKER = "LEVER DOCTOR FAILED"
VIEWS = (
    "pipeline",
    "cache",
    "effort",
    "tools",
    "friction",
    "guardrails",
    "provider",
    "recurrence",
    "intent",
    "interventions",
)


def _lever(work: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "scripts/fresh-install/sandbox.sh",
            "lever",
            "--data-dir",
            str(work / "data"),
            "--evidence-dir",
            str(work / "evidence"),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=LEVER_TIMEOUT_SECONDS,
        check=False,
    )


def test_a_new_machine_installs_and_every_dashboard_view_answers(tmp_path: Path) -> None:
    lever = _lever(tmp_path)
    evidence = tmp_path / "evidence"

    assert DOCTOR_MARKER not in lever.stderr, f"blocked infrastructure: {lever.stderr}"
    assert lever.returncode == 0, (evidence / "verify.log").read_text(encoding="utf-8")
    assert "verify: passed" in (evidence / "verify.log").read_text(encoding="utf-8")
    for view in VIEWS:
        response = json.loads(
            (evidence / "state" / "verify" / f"view-{view}.json").read_text(encoding="utf-8")
        )
        assert response["view"] == view
        assert response["errors"] == {}
    assert not (tmp_path / "data").exists()

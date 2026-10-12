"""Run the SQL command-shape fragment in ClickHouse against the shared fixture.

Opt in with ``AGENT_INTROSPECTION_LIVE_CLICKHOUSE=1``; the query only selects over
inline ``values()`` rows, so it writes nothing.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from agent_introspection import facts

CASES = json.loads((Path(__file__).parent / "fixtures" / "command_shape.json").read_text())
CONTAINER = "signoz-clickhouse"

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENT_INTROSPECTION_LIVE_CLICKHOUSE") != "1",
    reason="set AGENT_INTROSPECTION_LIVE_CLICKHOUSE=1 to run against the local ClickHouse",
)


def _literal(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"


def test_sql_fragment_matches_the_python_shape_for_every_fixture_case() -> None:
    fragment = facts._command_shape_fragment()
    rows = ", ".join(f"({_literal(case['command'])})" for case in CASES)
    query = (
        f"WITH {fragment}\n1 AS done\n"
        "SELECT cmd, command_head, command_sub, segments, verification_kinds, gate_run\n"
        f"FROM values('cmd String', {rows})"
    )
    result = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "clickhouse-client", "--format", "JSONEachRow"],
        input=query,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    got = {row["cmd"]: row for row in map(json.loads, result.stdout.splitlines())}

    for case in CASES:
        row = got[case["command"]]
        assert {
            "head": row["command_head"],
            "sub": row["command_sub"],
            "segments": int(row["segments"]),
            "verification_kinds": row["verification_kinds"],
            "gate_run": int(row["gate_run"]),
        } == {
            key: case[key] for key in ("head", "sub", "segments", "verification_kinds", "gate_run")
        }, case["command"]

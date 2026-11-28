from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from agent_introspection import facts

CASES = json.loads((Path(__file__).parent / "fixtures" / "command_shape.json").read_text())
CONTAINER_SETTING = "AGENT_INTROSPECTION_TEST_CLICKHOUSE"

pytestmark = [pytest.mark.boundary, pytest.mark.deferred]


def _literal(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"


def _container() -> str:
    container = os.environ.get(CONTAINER_SETTING)
    if not container:
        pytest.fail(
            f"{CONTAINER_SETTING} names no ClickHouse container; run "
            "bash scripts/run-ci-quality-gates.sh install, which starts one"
        )
    return container


def _clickhouse_query(query: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "exec", "-i", _container(), "clickhouse-client", "--format", "JSONEachRow"],
        input=query,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )


def test_sql_fragment_matches_the_python_shape_for_every_fixture_case() -> None:
    fragment = facts._command_shape_fragment()
    rows = ", ".join(f"({_literal(case['command'])})" for case in CASES)
    query = (
        f"WITH {fragment}\n1 AS done\n"
        "SELECT cmd, command_head, command_sub, segments, verification_kinds, gate_run\n"
        f"FROM values('cmd String', {rows})"
    )
    result = _clickhouse_query(query)
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

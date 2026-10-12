from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_introspection import command_shape

CASES = json.loads((Path(__file__).parent / "fixtures" / "command_shape.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=[case["command"][:40] for case in CASES])
def test_command_shape_matches_the_shared_fixture(case: dict[str, object]) -> None:
    shape = command_shape.command_shape(str(case["command"]))

    assert {
        "head": shape.head,
        "sub": shape.sub,
        "segments": shape.segments,
        "verification_kinds": list(shape.verification_kinds),
        "gate_run": shape.gate_run,
    } == {key: case[key] for key in ("head", "sub", "segments", "verification_kinds", "gate_run")}


def test_the_fixture_covers_every_verification_kind_and_a_gate_run() -> None:
    kinds = {kind for case in CASES for kind in case["verification_kinds"]}

    assert kinds == {rule[0] for rule in command_shape.KIND_RULES}
    assert any(case["gate_run"] for case in CASES)
    assert any(case["gate_run"] == 0 and case["command"] == "npm ci" for case in CASES)


def test_command_tokens_keep_paths_for_target_extraction_only() -> None:
    assert command_shape.command_tokens("FOO=1 cat a.py && b") == ["cat", "a.py", "&&", "b"]

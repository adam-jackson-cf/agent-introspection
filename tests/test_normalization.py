from __future__ import annotations

import pytest

from agent_introspection.normalization import (
    NormalizationError,
    normalize_shell_operation,
    normalize_structure,
    normalize_tool_operation,
)


def test_structured_normalization_removes_only_declared_volatility() -> None:
    normalized = normalize_structure(
        {
            "call_id": "stable-looking-value",
            "count": 42,
            "generated": "c56a4180-65aa-42ec-a945-5fd21dec0538",
            "path": "src/module.py:81:3",
            "semantic_id": "release-2026-07",
            "timestamp": "not-even-a-time",
            "when": "2026-07-10T10:30:00+01:00",
        }
    )
    assert normalized == {
        "call_id": "<call_id>",
        "count": 42,
        "generated": "<generated-id>",
        "path": "src/module.py",
        "semantic_id": "release-2026-07",
        "timestamp": "<timestamp>",
        "when": "<timestamp>",
    }


def test_shell_normalization_preserves_executable_subcommand_target_and_diagnostic() -> None:
    operation = normalize_shell_operation(
        {"cmd": "ruff check --output-format concise src/package.py:10:2"},
        exit_code=1,
        diagnostic_code="E501",
    )
    assert operation.executable == "ruff"
    assert operation.subcommand == "check"
    assert operation.target == "src/package.py"
    assert operation.exit_code == 1
    assert operation.diagnostic_code == "E501"
    assert "--output-format" in operation.argv


def test_normalized_membership_changes_for_semantic_command_changes() -> None:
    check = normalize_shell_operation({"cmd": ["pytest", "tests/unit"]})
    collect = normalize_shell_operation({"cmd": ["pytest", "--collect-only", "tests/unit"]})
    other_target = normalize_shell_operation({"cmd": ["pytest", "tests/integration"]})
    assert check.membership_key != collect.membership_key
    assert check.membership_key != other_target.membership_key


def test_shell_accepts_structured_argv_forms() -> None:
    direct = normalize_shell_operation(["ruff", "check", "src"])
    wrapped = normalize_shell_operation({"argv": ["ruff", "check", "src"]})
    assert direct.membership_key == wrapped.membership_key


def test_shell_target_extraction_ignores_option_values_and_preserves_metadata() -> None:
    operation = normalize_shell_operation(
        {"cmd": ["git", "-C", "/tmp/generated", "status"]},
        exit_code=-9,
        diagnostic_code="SIGKILL",
    )
    assert operation.executable == "git"
    assert operation.subcommand == "status"
    assert operation.target is None
    assert operation.exit_code == -9
    assert operation.diagnostic_code == "SIGKILL"

    commit = normalize_shell_operation({"cmd": ["git", "commit", "-m", "message"]})
    assert commit.subcommand == "commit"
    assert commit.target is None

    pytest_operation = normalize_shell_operation({"cmd": ["pytest", "tests\\unit"]})
    assert pytest_operation.subcommand is None
    assert pytest_operation.target == "tests/unit"


def test_structured_normalization_rejects_nonfinite_and_preserves_uri_targets() -> None:
    with pytest.raises(NormalizationError, match="non-finite"):
        normalize_structure({"limit": float("nan")})
    operation = normalize_tool_operation(
        "fetch_resource",
        {"uri": "https://example.test/a:b", "timestamp": "stable"},
    )
    assert operation.target == "https://example.test/a:b"
    assert '"timestamp":"<timestamp>"' in operation.argv[0]


def test_tool_normalization_is_order_independent_and_target_aware() -> None:
    left = normalize_tool_operation("read_file", '{"path":"src/a.py","limit":10}')
    right = normalize_tool_operation("read_file", {"limit": 10, "path": "src/a.py"})
    assert left.membership_key == right.membership_key
    assert left.target == "src/a.py"


@pytest.mark.parametrize(
    "arguments",
    ["plain shell text", "null", "{}", {"cmd": "unterminated '"}],
)
def test_invalid_or_unstructured_shell_arguments_fail_closed(arguments: object) -> None:
    with pytest.raises(NormalizationError):
        normalize_shell_operation(arguments)  # type: ignore[arg-type]

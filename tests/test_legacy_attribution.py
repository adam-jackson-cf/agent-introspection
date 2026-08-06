from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_introspection.cli import _parser
from agent_introspection.config import ConfigurationError, parse_config
from agent_introspection.database import connect_database
from agent_introspection.legacy_attribution import (
    LEGACY_PROJECT_ATTRIBUTION_QUERY,
    _parse_candidate,
    parse_rfc3339,
    run_legacy_project_attribution,
)


def test_cli_requires_explicit_legacy_run_arguments() -> None:
    parser = _parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["legacy-project-attribution", "run"])
    parsed = parser.parse_args(
        [
            "legacy-project-attribution",
            "run",
            "--start",
            "2026-01-01T00:00:00Z",
            "--end",
            "2026-01-01T01:00:00Z",
            "--approved-by",
            "operator",
        ]
    )
    assert parsed.approved_by == "operator"


def test_config_requires_well_formed_manual_roots(tmp_path: Path) -> None:
    config = parse_config(
        {"legacy_project_attribution": {"project_roots": [str(tmp_path)], "maximum_range_hours": 2}}
    )
    assert config.legacy_project_attribution.project_roots == (tmp_path.resolve(),)
    assert config.legacy_project_attribution.maximum_range_hours == 2
    with pytest.raises(ConfigurationError, match="array of non-empty paths"):
        parse_config({"legacy_project_attribution": {"project_roots": [""]}})


def test_rfc3339_requires_timezone_and_range_is_bounded() -> None:
    with pytest.raises(ValueError, match="timezone"):
        parse_rfc3339("2026-01-01T00:00:00")
    config = parse_config(
        {"legacy_project_attribution": {"project_roots": ["/tmp"], "maximum_range_hours": 1}}
    )
    with pytest.raises(ValueError, match="Maximum supported"):
        run_legacy_project_attribution(
            object(),
            config,
            client=object(),
            start=datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime(2026, 1, 1, 2, tzinfo=UTC),
            approved_by="operator",
        )


@pytest.mark.parametrize(
    "row",
    [
        {},
        {
            "log_id": "log",
            "correlation_id": "thread",
            "call_id": "call",
            "tool_name": "other",
            "timestamp": 1,
            "arguments": "{}",
        },
        {
            "log_id": "log",
            "correlation_id": "thread",
            "call_id": "call",
            "tool_name": "exec",
            "timestamp": 1,
            "arguments": "not-json",
        },
        {
            "log_id": "log",
            "correlation_id": "thread",
            "call_id": "call",
            "tool_name": "exec",
            "timestamp": 1,
            "arguments": json.dumps({"workdir": "/tmp", "cmd": "cat x", "output": "secret"}),
        },
    ],
)
def test_malformed_or_non_allowlisted_rows_are_rejected(row: dict[str, object]) -> None:
    assert _parse_candidate(row) is None


def test_fixed_query_excludes_scan_scheduler_and_sensitive_projection() -> None:
    assert "scan" not in LEGACY_PROJECT_ATTRIBUTION_QUERY.lower()
    assert "scheduler" not in LEGACY_PROJECT_ATTRIBUTION_QUERY.lower()
    assert "prompt" not in LEGACY_PROJECT_ATTRIBUTION_QUERY.lower()
    assert "output" not in LEGACY_PROJECT_ATTRIBUTION_QUERY.lower()
    assert "environment" not in LEGACY_PROJECT_ATTRIBUTION_QUERY.lower()


def test_manual_writer_persists_only_canonical_fields_and_refuses_duplicate(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "tracked.py").write_text("pass\n", encoding="utf-8")
    subprocess.run(("git", "init", str(workspace)), check=True, capture_output=True, text=True)
    config = parse_config(
        {
            "database": {"path": str(tmp_path / "state.sqlite3")},
            "legacy_project_attribution": {"project_roots": [str(tmp_path)]},
        }
    )

    class Client:
        def query(self, _sql: str, _parameters: dict[str, int]):
            return iter(
                [
                    {
                        "timestamp": 1_700_000_000_000_000_000,
                        "log_id": "safe-log",
                        "correlation_id": "thread",
                        "call_id": "call",
                        "tool_name": "exec",
                        "arguments": json.dumps(
                            {
                                "cmd": "python tracked.py",
                                "workdir": str(workspace),
                                "yield_time_ms": 10_000,
                                "max_output_chars": 3_414,
                            }
                        ),
                    }
                ]
            )

    connection = connect_database(config.database.path)
    start = datetime(2023, 11, 14, tzinfo=UTC)
    end = start + timedelta(minutes=1)
    result = run_legacy_project_attribution(
        connection, config, client=Client(), start=start, end=end, approved_by="operator"
    )
    assert result["accepted"] == 1
    assert result["rejected"] == result["unresolved"] == 0
    assert (
        result["denominator"] == len(result["activity_ids"]) == len(result["outbox_event_ids"]) == 1
    )
    activity = connection.execute(
        "SELECT normalized_target, source_membership_json FROM canonical_activities"
    ).fetchone()
    assert activity == (".", '{"event_ids":[],"log_ids":["safe-log"],"span_ids":[]}')
    assert connection.execute("SELECT count(*) FROM otlp_outbox").fetchone() == (1,)
    fact = connection.execute(
        """
        SELECT approved_by, denominator, accepted, rejected, unresolved, source_ids_json
        FROM legacy_attribution_fact_sets
        """
    ).fetchone()
    assert fact == ("operator", 1, 1, 0, 0, '["safe-log"]')
    with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
        connection.execute("DELETE FROM legacy_attribution_fact_sets")
    with pytest.raises(RuntimeError, match="already applied"):
        run_legacy_project_attribution(
            connection, config, client=Client(), start=start, end=end, approved_by="operator"
        )
    connection.close()

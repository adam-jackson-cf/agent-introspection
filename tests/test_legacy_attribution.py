from __future__ import annotations

import json
import sqlite3
import subprocess
import urllib.error
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agent_introspection.cli import _parser
from agent_introspection.config import ConfigurationError, parse_config
from agent_introspection.database import connect_database
from agent_introspection.legacy_attribution import (
    LEGACY_PROJECT_ATTRIBUTION_QUERY,
    LegacyProjectAttributionRequest,
    _parse_candidate,
    parse_rfc3339,
    recover_legacy_project_attribution,
    run_legacy_project_attribution,
)
from agent_introspection.source import ClickHouseClient


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
    recovery = parser.parse_args(
        ["legacy-project-attribution", "recover", "--fact-set-id", "fact-set"]
    )
    assert recovery.fact_set_id == "fact-set"
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
    connection = sqlite3.connect(":memory:")
    with pytest.raises(ValueError, match="Maximum supported"):
        run_legacy_project_attribution(
            connection,
            ClickHouseClient(docker_context="test"),
            LegacyProjectAttributionRequest(
                config=config,
                start=datetime(2026, 1, 1, tzinfo=UTC),
                end=datetime(2026, 1, 1, 2, tzinfo=UTC),
                approved_by="operator",
            ),
        )
    connection.close()


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

    class Client(ClickHouseClient):
        def __init__(self) -> None:
            pass

        def query(self, sql: str, parameters: Mapping[str, str | int]) -> Iterator[dict[str, Any]]:
            if "attributes_string['event.id']" in sql:
                yield {"event_id": parameters["event_0"]}
                return
            yield {
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

    connection = connect_database(config.database.path)
    start = datetime(2023, 11, 14, tzinfo=UTC)
    end = start + timedelta(minutes=1)
    response = MagicMock(status=200)
    response.__enter__.return_value = response
    with patch("urllib.request.urlopen", return_value=response):
        result = run_legacy_project_attribution(
            connection,
            Client(),
            LegacyProjectAttributionRequest(
                config=config, start=start, end=end, approved_by="operator"
            ),
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
            connection,
            Client(),
            LegacyProjectAttributionRequest(
                config=config, start=start, end=end, approved_by="operator"
            ),
        )
    connection.close()


def test_manual_writer_recovers_transport_failure_with_exact_immutable_event_set(
    tmp_path: Path,
) -> None:
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
    remote_ready = False

    class Client(ClickHouseClient):
        def __init__(self) -> None:
            pass

        def query(self, sql: str, parameters: Mapping[str, str | int]) -> Iterator[dict[str, Any]]:
            if "attributes_string['event.id']" in sql:
                if remote_ready:
                    yield {"event_id": parameters["event_0"]}
                return
            yield {
                "timestamp": 1_700_000_000_000_000_000,
                "log_id": "safe-log",
                "correlation_id": "thread",
                "call_id": "call",
                "tool_name": "exec",
                "arguments": json.dumps({"cmd": "python tracked.py", "workdir": str(workspace)}),
            }

    connection = connect_database(config.database.path)
    start = datetime(2023, 11, 14, tzinfo=UTC)
    try:
        with (
            patch(
                "urllib.request.urlopen",
                side_effect=urllib.error.URLError("injected transport failure"),
            ),
            pytest.raises(RuntimeError, match="local_delivery_incomplete"),
        ):
            run_legacy_project_attribution(
                connection,
                Client(),
                LegacyProjectAttributionRequest(
                    config=config,
                    start=start,
                    end=start + timedelta(minutes=1),
                    approved_by="operator",
                ),
            )

        fact_set_id = connection.execute("SELECT id FROM legacy_attribution_fact_sets").fetchone()[
            0
        ]
        event_id = connection.execute("SELECT event_id FROM otlp_outbox").fetchone()[0]
        attempt = connection.execute(
            """
            SELECT intended_event_ids_json, intended_event_count, local_delivery_result_json,
                   remote_event_ids_json, failure_reason, verified_at
            FROM legacy_attribution_delivery_attempts
            """
        ).fetchone()
        assert attempt == (
            json.dumps([event_id], separators=(",", ":")),
            1,
            '{"delivered":0,"pending":1,"selected":1}',
            "[]",
            "local_delivery_incomplete",
            None,
        )
        assert connection.execute("SELECT count(*) FROM canonical_activities").fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM canonical_activity_versions"
        ).fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM otlp_outbox").fetchone() == (1,)

        remote_ready = True
        response = MagicMock(status=200)
        response.__enter__.return_value = response
        with patch("urllib.request.urlopen", return_value=response):
            recovered = recover_legacy_project_attribution(
                connection, client=Client(), fact_set_id=fact_set_id
            )
        assert recovered["status"] == "verified"
        assert recovered["idempotent"] is False
        assert recovered["intended_event_count"] == recovered["remote_event_count"] == 1
        attempts = connection.execute(
            """
            SELECT intended_event_ids_json, remote_event_ids_json, failure_reason, verified_at
            FROM legacy_attribution_delivery_attempts
            ORDER BY id
            """
        ).fetchall()
        assert attempts[1][0] == attempts[1][1] == json.dumps([event_id], separators=(",", ":"))
        assert attempts[1][2] is None
        assert attempts[1][3] is not None
        assert connection.execute("SELECT event_id, status FROM otlp_outbox").fetchone() == (
            event_id,
            "delivered",
        )
        assert connection.execute("SELECT count(*) FROM canonical_activities").fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM canonical_activity_versions"
        ).fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM otlp_outbox").fetchone() == (1,)
        assert recover_legacy_project_attribution(
            connection, client=Client(), fact_set_id=fact_set_id
        ) == {"status": "verified", "fact_set_id": fact_set_id, "idempotent": True}
    finally:
        connection.close()


def test_manual_writer_refuses_remote_event_id_mismatch_after_delivery(tmp_path: Path) -> None:
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

    class Client(ClickHouseClient):
        def __init__(self) -> None:
            pass

        def query(self, sql: str, parameters: Mapping[str, str | int]) -> Iterator[dict[str, Any]]:
            if "attributes_string['event.id']" in sql:
                return
            yield {
                "timestamp": 1_700_000_000_000_000_000,
                "log_id": "safe-log",
                "correlation_id": "thread",
                "call_id": "call",
                "tool_name": "exec",
                "arguments": json.dumps({"cmd": "python tracked.py", "workdir": str(workspace)}),
            }

    connection = connect_database(config.database.path)
    response = MagicMock(status=200)
    response.__enter__.return_value = response
    start = datetime(2023, 11, 14, tzinfo=UTC)
    try:
        with (
            patch("urllib.request.urlopen", return_value=response),
            pytest.raises(RuntimeError, match="remote_event_id_mismatch"),
        ):
            run_legacy_project_attribution(
                connection,
                Client(),
                LegacyProjectAttributionRequest(
                    config=config,
                    start=start,
                    end=start + timedelta(minutes=1),
                    approved_by="operator",
                ),
            )
        fact_count = connection.execute(
            "SELECT count(*) FROM legacy_attribution_fact_sets"
        ).fetchone()
        assert fact_count == (1,)
        assert connection.execute("SELECT status FROM otlp_outbox").fetchone() == ("delivered",)
        attempt = connection.execute(
            """
            SELECT intended_event_count, remote_event_count, failure_reason, verified_at
            FROM legacy_attribution_delivery_attempts
            """
        ).fetchone()
        assert attempt == (1, 0, "remote_event_id_mismatch", None)
        fact_set_id = connection.execute("SELECT id FROM legacy_attribution_fact_sets").fetchone()[
            0
        ]

        class RecoveryClient(ClickHouseClient):
            def __init__(self) -> None:
                pass

            def query(
                self, sql: str, parameters: Mapping[str, str | int]
            ) -> Iterator[dict[str, Any]]:
                assert "attributes_string['event.id']" in sql
                yield {"event_id": parameters["event_0"]}

        with patch("urllib.request.urlopen", return_value=response):
            recovered = recover_legacy_project_attribution(
                connection, client=RecoveryClient(), fact_set_id=fact_set_id
            )
        assert recovered["status"] == "verified"
        assert recovered["idempotent"] is False
        assert connection.execute(
            "SELECT count(*) FROM canonical_activity_versions"
        ).fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM canonical_activities").fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM legacy_attribution_delivery_attempts"
        ).fetchone() == (2,)
        assert recover_legacy_project_attribution(
            connection, client=RecoveryClient(), fact_set_id=fact_set_id
        ) == {"status": "verified", "fact_set_id": fact_set_id, "idempotent": True}
    finally:
        connection.close()

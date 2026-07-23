import copy
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import agent_introspection.capabilities as capabilities
from agent_introspection.capabilities import (
    CapabilityError,
    approve_schema,
    discover_source_schema,
    enforce_approved_schema,
    schema_fingerprint,
)
from agent_introspection.database import connect_database
from agent_introspection.source import HYDRATION_QUERIES, LOG_QUERY, TRACE_QUERY, ClickHouseClient


class SourceSchemaClient(ClickHouseClient):
    def __init__(self) -> None:
        self.timezone = "UTC"
        required = {
            ("signoz_logs", "distributed_logs_v2"): {
                "attributes_bool",
                "attributes_number",
                "attributes_string",
                "id",
                "resource",
                "span_id",
                "timestamp",
                "trace_id",
                "ts_bucket_start",
            },
            ("signoz_traces", "distributed_signoz_index_v3"): {
                "attributes_number",
                "attributes_string",
                "name",
                "serviceName",
                "timestamp",
                "trace_id",
                "ts_bucket_start",
            },
        }
        self.columns = [
            {
                "database": database,
                "table": table,
                "name": name,
                "type": "String",
                "default_kind": "",
                "default_expression": "",
            }
            for (database, table), names in required.items()
            for name in sorted(names)
        ]
        self.tables = [
            {
                "database": "signoz_logs",
                "name": "distributed_logs_v2",
                "engine": "Distributed",
                "create_table_query": "CREATE TABLE logs",
            },
            {
                "database": "signoz_traces",
                "name": "distributed_signoz_index_v3",
                "engine": "Distributed",
                "create_table_query": "CREATE TABLE traces",
            },
        ]
        self.log_diagnostics = [{"event_names": ["codex.tool_result"]}]
        self.trace_diagnostics = [{"event_names": ["session_task.turn"]}]

    def query(self, sql: str, _parameters: object) -> Iterator[dict[str, Any]]:
        if "timezone()" in sql:
            return iter([{"timezone": self.timezone}])
        if "system.columns" in sql:
            return iter(copy.deepcopy(self.columns))
        if "system.tables" in sql:
            return iter(copy.deepcopy(self.tables))
        if "FROM signoz_logs" in sql:
            return iter(copy.deepcopy(self.log_diagnostics))
        if "FROM signoz_traces" in sql:
            return iter(copy.deepcopy(self.trace_diagnostics))
        raise AssertionError(f"unexpected query: {sql}")


def test_diagnostics_and_unrelated_structure_do_not_change_approved_contract(
    tmp_path: Path,
) -> None:
    client = SourceSchemaClient()
    initial = discover_source_schema(client)
    connection = connect_database(tmp_path / "introspection.sqlite3")
    fingerprint = approve_schema(connection, initial, approved_by="test")

    client.log_diagnostics = [{"event_names": ["codex.tool_result", "new.event"]}]
    client.trace_diagnostics = [{"event_names": ["new.span"]}]
    client.columns.append(
        {
            "database": "signoz_logs",
            "table": "distributed_logs_v2",
            "name": "unrelated_column",
            "type": "UInt64",
            "default_kind": "",
            "default_expression": "",
        }
    )
    client.tables[0]["engine"] = "OtherMetadata"
    client.tables[0]["create_table_query"] = "unrelated table metadata"
    rediscovered = discover_source_schema(client)

    assert rediscovered["diagnostics"] != initial["diagnostics"]
    assert schema_fingerprint(rediscovered) == fingerprint
    assert enforce_approved_schema(connection, rediscovered) == fingerprint
    persisted = connection.execute(
        "SELECT schema_json FROM source_schema_snapshots WHERE fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0]
    assert json.loads(persisted) == initial["contract"]
    assert "diagnostics" not in json.loads(persisted)


@pytest.mark.parametrize(
    "contract_path,replacement",
    [
        (("timezone",), "Europe/London"),
        (("tables", 0, "table"), "renamed_logs"),
        (("columns", 0, "type"), "UInt64"),
        (("columns", 0, "default_kind"), "DEFAULT"),
        (("columns", 0, "default_expression"), "0"),
    ],
)
def test_canonical_contract_drift_changes_fingerprint_and_fails_closed(
    tmp_path: Path,
    contract_path: tuple[str | int, ...],
    replacement: str,
) -> None:
    discovery = discover_source_schema(SourceSchemaClient())
    connection = connect_database(tmp_path / "introspection.sqlite3")
    approved = approve_schema(connection, discovery, approved_by="test")
    changed = copy.deepcopy(discovery)
    target: Any = changed["contract"]
    for key in contract_path[:-1]:
        target = target[key]
    target[contract_path[-1]] = replacement

    assert schema_fingerprint(changed) != approved
    with pytest.raises(CapabilityError, match="schema drift"):
        enforce_approved_schema(connection, changed)


def test_missing_required_column_fails_discovery() -> None:
    client = SourceSchemaClient()
    client.columns.pop()

    with pytest.raises(CapabilityError, match="required SigNoz source columns"):
        discover_source_schema(client)


@pytest.mark.parametrize(
    "query_class",
    ["log", "trace", "hydration.log_id", "hydration.trace_id", "hydration.call_id"],
)
def test_each_extraction_query_class_participates_in_contract(
    monkeypatch: pytest.MonkeyPatch,
    query_class: str,
) -> None:
    baseline = schema_fingerprint(discover_source_schema(SourceSchemaClient()))
    if query_class == "log":
        monkeypatch.setattr(capabilities, "LOG_QUERY", LOG_QUERY + "\n-- contract mutation")
    elif query_class == "trace":
        monkeypatch.setattr(capabilities, "TRACE_QUERY", TRACE_QUERY + "\n-- contract mutation")
    else:
        identity = query_class.removeprefix("hydration.")
        queries: dict[str, str] = dict(HYDRATION_QUERIES)
        queries[identity] += "\n-- contract mutation"
        monkeypatch.setattr(capabilities, "HYDRATION_QUERIES", queries)

    assert schema_fingerprint(discover_source_schema(SourceSchemaClient())) != baseline

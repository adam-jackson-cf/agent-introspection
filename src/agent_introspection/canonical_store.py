"""Non-expiring, ledger-bound Pipeline authority delivered through real OTLP ingestion."""

from __future__ import annotations

import re
from typing import Any

from agent_introspection.ledger_identity import DATABASE_IDENTITY_ATTRIBUTE
from agent_introspection.pipeline_integrity import PIPELINE_AUTHORITY_EVENTS
from agent_introspection.source import ClickHouseClient

_DATABASE = "agent_introspection"
_COLUMNS = (
    "timestamp",
    "ts_bucket_start",
    "body",
    "attributes_string",
    "attributes_number",
    "attributes_bool",
    "resource",
)
_SELECT_COLUMNS = ", ".join(_COLUMNS)
_SORTING_KEY = "timestamp, attributes_string['event.id']"
_IDENTITY = re.compile(r"[a-f0-9]{64}\Z")
_EVENT_NAMES_SQL = ", ".join(f"'{name}'" for name in sorted(PIPELINE_AUTHORITY_EVENTS))


def _ingest_select(database_id: str) -> str:
    if _IDENTITY.fullmatch(database_id) is None:
        raise ValueError("canonical authority requires a valid ledger identity")
    # Preserve malformed observations claiming Pipeline authority for strict reducers.
    return f"""SELECT {_SELECT_COLUMNS}
FROM signoz_logs.logs_v2
WHERE resource.`service.name`::String = 'agent-introspection'
  AND resource.`{DATABASE_IDENTITY_ATTRIBUTE}`::String = '{database_id}'
  AND (
    attributes_string['event.name'] IN ({_EVENT_NAMES_SQL})
    OR mapContains(attributes_string, 'pipeline.payload_schema_version')
    OR mapContains(attributes_number, 'pipeline.payload_schema_version')
    OR mapContains(attributes_bool, 'pipeline.payload_schema_version')
  )"""


def _object(client: ClickHouseClient, name: str) -> dict[str, Any]:
    rows = list(
        client.query(
            "SELECT engine, sorting_key, partition_key, comment, create_table_query, as_select "
            "FROM system.tables WHERE database = {database:String} AND name = {name:String}",
            {"database": _DATABASE, "name": name},
        )
    )
    if len(rows) != 1:
        raise ValueError(f"canonical authority object is unavailable: {name}")
    return rows[0]


def _check_events(client: ClickHouseClient, database_id: str) -> None:
    table = _object(client, "events")
    if (
        table["engine"] != "MergeTree"
        or "".join(table["sorting_key"].split()) != "".join(_SORTING_KEY.split())
        or table["partition_key"]
        or re.search(r"\bTTL\b", table["create_table_query"])
        or table["comment"] != f"{DATABASE_IDENTITY_ATTRIBUTE}={database_id}"
    ):
        raise ValueError("canonical events have incompatible storage, ownership, or expiration")
    source_columns = {
        row["name"]: row["type"]
        for row in client.query(
            "SELECT name, type FROM system.columns "
            "WHERE database = 'signoz_logs' AND table = 'logs_v2'",
            {},
        )
        if row["name"] in _COLUMNS
    }
    if set(source_columns) != set(_COLUMNS):
        raise ValueError("canonical authority source schema is incomplete")
    columns = list(
        client.query(
            "SELECT name, type, default_kind FROM system.columns "
            "WHERE database = {database:String} AND table = 'events'",
            {"database": _DATABASE},
        )
    )
    if {row["name"]: row["type"] for row in columns} != source_columns or any(
        row["default_kind"] for row in columns
    ):
        raise ValueError("canonical events do not preserve the source column types")


def _check_ingestion(client: ClickHouseClient, select: str) -> None:
    view = _object(client, "ingest_events")
    if view["engine"] != "MaterializedView" or not re.search(
        rf"\bTO\s+{_DATABASE}\.events\b", view["create_table_query"]
    ):
        raise ValueError("canonical authority ingestion has an incompatible destination")
    expected = select.replace("\\", "\\\\").replace("'", "\\'")
    comparison = list(
        client.query(
            "SELECT formatQuerySingleLine(as_select) "
            f"= formatQuerySingleLine('{expected}') AS matches "
            "FROM system.tables WHERE database = 'agent_introspection' AND name = 'ingest_events'",
            {},
        )
    )
    if len(comparison) != 1 or comparison[0]["matches"] != 1:
        raise ValueError("canonical authority ingestion has incompatible ownership or selection")


def check_authority_store(client: ClickHouseClient, *, database_id: str) -> dict[str, str]:
    """Verify no expiration, exact publication selection, and the configured ledger owner."""
    select = _ingest_select(database_id)
    _check_events(client, database_id)
    _check_ingestion(client, select)
    return {
        "status": "ready",
        "database": _DATABASE,
        "database_identity": database_id,
        "retention": "no expiry",
    }


def configure_authority_store(client: ClickHouseClient, *, database_id: str) -> dict[str, str]:
    """Create authority storage without replacing objects or changing native retention."""
    select = _ingest_select(database_id)
    statements = (
        f"CREATE DATABASE IF NOT EXISTS {_DATABASE}",
        f"CREATE TABLE IF NOT EXISTS {_DATABASE}.events "
        f"ENGINE = MergeTree ORDER BY ({_SORTING_KEY}) "
        f"AS {select} AND 0 COMMENT '{DATABASE_IDENTITY_ATTRIBUTE}={database_id}'",
    )
    for statement in statements:
        list(client.query(statement, {}))
    _check_events(client, database_id)
    list(
        client.query(
            f"CREATE MATERIALIZED VIEW IF NOT EXISTS {_DATABASE}.ingest_events "
            f"TO {_DATABASE}.events AS {select}",
            {},
        )
    )
    return check_authority_store(client, database_id=database_id)

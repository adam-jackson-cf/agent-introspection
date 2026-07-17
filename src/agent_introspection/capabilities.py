"""Fail-closed SigNoz, schema and model capability verification."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agent_introspection.source import HYDRATION_QUERIES, LOG_QUERY, TRACE_QUERY, ClickHouseClient

_REQUIRED_SOURCE_COLUMNS = {
    ("signoz_logs", "distributed_logs_v2"): frozenset(
        {
            "attributes_bool",
            "attributes_number",
            "attributes_string",
            "id",
            "resource",
            "span_id",
            "timestamp",
            "trace_id",
            "ts_bucket_start",
        }
    ),
    ("signoz_traces", "distributed_signoz_index_v3"): frozenset(
        {
            "attributes_number",
            "attributes_string",
            "name",
            "serviceName",
            "timestamp",
            "trace_id",
            "ts_bucket_start",
        }
    ),
}


class CapabilityError(RuntimeError):
    """A required capability is unavailable or unproven."""


@dataclass(frozen=True, slots=True)
class ModelCapabilityProof:
    model: str
    effort: str
    thread_id: str
    trace_id: str
    total_tokens: int
    tool_version: str
    schema_fingerprint: str
    proven_at: datetime


def check_health(url: str, *, timeout_seconds: float = 5) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise CapabilityError("SigNoz health check failed") from exc
    if not isinstance(payload, dict) or response.status != 200 or payload.get("status") != "ok":
        raise CapabilityError("SigNoz health response is not healthy")
    return {str(key): value for key, value in payload.items()}


def start_signoz(*, compose_directory: Path, docker_context: str) -> None:
    compose = compose_directory / "docker-compose.yaml"
    override = compose_directory / "docker-compose.override.yaml"
    if not compose.is_file() or not override.is_file():
        raise CapabilityError("canonical SigNoz Compose files are missing")
    result = subprocess.run(
        [
            "docker",
            "--context",
            docker_context,
            "compose",
            "--project-directory",
            str(compose_directory),
            "up",
            "--detach",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        diagnostic = result.stderr.strip().splitlines()[-1:] or ["unknown Docker error"]
        raise CapabilityError(f"SigNoz start failed: {diagnostic[0]}")


def ensure_health(
    *,
    health_url: str,
    compose_directory: Path,
    docker_context: str,
) -> dict[str, Any]:
    try:
        return check_health(health_url)
    except CapabilityError:
        start_signoz(compose_directory=compose_directory, docker_context=docker_context)
        return check_health(health_url, timeout_seconds=15)


def verify_network_perimeter(*, docker_context: str) -> dict[str, Any]:
    """Require loopback-only SigNoz/OTLP publication and disabled OrbStack LAN exposure."""
    expected = {
        "signoz": {"8080/tcp": {("127.0.0.1", "8080")}},
        "signoz-otel-collector": {
            "4317/tcp": {("127.0.0.1", "4317")},
            "4318/tcp": {("127.0.0.1", "4318")},
        },
    }
    observed: dict[str, dict[str, set[tuple[str, str]]]] = {}
    for container, required in expected.items():
        result = subprocess.run(
            [
                "docker",
                "--context",
                docker_context,
                "inspect",
                container,
                "--format",
                "{{json .HostConfig.PortBindings}}",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise CapabilityError(f"cannot inspect network bindings for {container}")
        try:
            bindings = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise CapabilityError(f"invalid network bindings for {container}") from exc
        normalized = {
            port: {(entry.get("HostIp", ""), entry.get("HostPort", "")) for entry in entries}
            for port, entries in bindings.items()
        }
        if normalized != required:
            raise CapabilityError(f"{container} is not published loopback-only")
        observed[container] = normalized

    orb = subprocess.run(
        ["orbctl", "config", "get", "docker.expose_ports_to_lan"],
        check=False,
        capture_output=True,
        text=True,
    )
    if orb.returncode != 0 or orb.stdout.strip() != "false":
        raise CapabilityError("OrbStack LAN port exposure is not disabled")
    return {
        "docker_context": docker_context,
        "loopback_only": True,
        "orbstack_lan_exposure": False,
    }


def discover_source_schema(client: ClickHouseClient) -> dict[str, Any]:
    """Capture the canonical source contract and ambient diagnostic evidence."""
    columns_sql = """
    SELECT database, table, name, type, default_kind, default_expression
    FROM system.columns
    WHERE (database, table) IN (
      ('signoz_logs', 'distributed_logs_v2'),
      ('signoz_traces', 'distributed_signoz_index_v3')
    )
    ORDER BY database, table, position
    """
    tables_sql = """
    SELECT database, name, engine, create_table_query
    FROM system.tables
    WHERE (database, name) IN (
      ('signoz_logs', 'distributed_logs_v2'),
      ('signoz_traces', 'distributed_signoz_index_v3')
    )
    ORDER BY database, name
    """
    log_semantics_sql = """
    SELECT
      arraySort(groupUniqArray(attributes_string['event.name'])) AS event_names,
      arraySort(groupUniqArrayArray(mapKeys(attributes_string))) AS string_attribute_keys,
      arraySort(groupUniqArrayArray(mapKeys(attributes_number))) AS number_attribute_keys,
      arraySort(groupUniqArrayArray(mapKeys(attributes_bool))) AS bool_attribute_keys
    FROM signoz_logs.distributed_logs_v2
    WHERE ts_bucket_start >= toUInt64(toUnixTimestamp(now() - INTERVAL 15 DAY))
      AND resource.`service.name`::String IN ('codex_cli_rs', 'codex-app-server')
    """
    trace_semantics_sql = """
    SELECT
      arraySort(groupUniqArray(name)) AS event_names,
      arraySort(groupUniqArrayArray(mapKeys(attributes_string))) AS string_attribute_keys,
      arraySort(groupUniqArrayArray(mapKeys(attributes_number))) AS number_attribute_keys
    FROM signoz_traces.distributed_signoz_index_v3
    WHERE timestamp >= now64(9) - INTERVAL 15 DAY
      AND serviceName IN ('codex_cli_rs', 'codex-app-server')
    """
    server = list(client.query("SELECT timezone() AS timezone", {}))
    columns = list(client.query(columns_sql, {}))
    tables = list(client.query(tables_sql, {}))
    if len(server) != 1 or not isinstance(server[0].get("timezone"), str):
        raise CapabilityError("SigNoz source timezone is unavailable")

    table_identities = {(row.get("database"), row.get("name")) for row in tables}
    if table_identities != set(_REQUIRED_SOURCE_COLUMNS):
        raise CapabilityError("required SigNoz source tables are unavailable")

    required_columns = []
    observed_columns: set[tuple[object, object, object]] = set()
    for row in columns:
        identity = (row.get("database"), row.get("table"))
        name = row.get("name")
        if identity in _REQUIRED_SOURCE_COLUMNS and name in _REQUIRED_SOURCE_COLUMNS[identity]:
            observed_columns.add((*identity, name))
            required_columns.append(
                {
                    "database": identity[0],
                    "table": identity[1],
                    "name": name,
                    "type": row.get("type"),
                    "default_kind": row.get("default_kind"),
                    "default_expression": row.get("default_expression"),
                }
            )
    expected_columns = {
        (*identity, name) for identity, names in _REQUIRED_SOURCE_COLUMNS.items() for name in names
    }
    if observed_columns != expected_columns:
        raise CapabilityError("required SigNoz source columns are unavailable")

    query_hashes = {
        "log": hashlib.sha256(LOG_QUERY.encode()).hexdigest(),
        "trace": hashlib.sha256(TRACE_QUERY.encode()).hexdigest(),
        "hydration": {
            identity: hashlib.sha256(query.encode()).hexdigest()
            for identity, query in sorted(HYDRATION_QUERIES.items())
        },
    }
    return {
        "contract": {
            "timezone": server[0]["timezone"],
            "tables": [
                {"database": database, "table": table}
                for database, table in sorted(_REQUIRED_SOURCE_COLUMNS)
            ],
            "columns": sorted(
                required_columns,
                key=lambda row: (str(row["database"]), str(row["table"]), str(row["name"])),
            ),
            "queries": query_hashes,
        },
        "diagnostics": {
            "columns": columns,
            "tables": tables,
            "logs": list(client.query(log_semantics_sql, {})),
            "traces": list(client.query(trace_semantics_sql, {})),
        },
    }


def schema_fingerprint(discovery: dict[str, Any]) -> str:
    contract = discovery.get("contract")
    if not isinstance(contract, dict):
        raise CapabilityError("source schema discovery has no canonical contract")
    canonical = json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def approve_schema(
    connection: sqlite3.Connection,
    discovery: dict[str, Any],
    *,
    approved_by: str,
) -> str:
    fingerprint = schema_fingerprint(discovery)
    contract = discovery["contract"]
    now = datetime.now(UTC).isoformat()
    existing = connection.execute(
        """
        SELECT id, approved_at FROM source_schema_snapshots
        WHERE source = 'signoz' AND fingerprint = ?
        """,
        (fingerprint,),
    ).fetchone()
    if existing is not None and existing[1] is not None:
        return fingerprint
    with connection:
        if existing is None:
            connection.execute(
                """
                INSERT INTO source_schema_snapshots (
                    id, source, fingerprint, schema_json, captured_at, approved_at, approved_by
                ) VALUES (?, 'signoz', ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    fingerprint,
                    json.dumps(contract, sort_keys=True, separators=(",", ":")),
                    now,
                    now,
                    approved_by,
                ),
            )
        else:
            connection.execute(
                """
                UPDATE source_schema_snapshots SET approved_at = ?, approved_by = ?
                WHERE id = ?
                """,
                (now, approved_by, existing[0]),
            )
    return fingerprint


def enforce_approved_schema(connection: sqlite3.Connection, discovery: dict[str, Any]) -> str:
    fingerprint = schema_fingerprint(discovery)
    row = connection.execute(
        """
        SELECT 1 FROM source_schema_snapshots
        WHERE source = 'signoz' AND fingerprint = ? AND approved_at IS NOT NULL
        """,
        (fingerprint,),
    ).fetchone()
    if row is None:
        raise CapabilityError("source schema drift detected or schema is not approved")
    return fingerprint


def store_model_proof(
    connection: sqlite3.Connection,
    proof: ModelCapabilityProof,
) -> None:
    if proof.model == "gpt-5.6-luna" and proof.effort != "medium":
        raise CapabilityError("GPT-5.6 Luna capability requires medium effort")
    if proof.model == "gpt-5.5" and proof.effort != "high":
        raise CapabilityError("GPT-5.5 capability requires high effort")
    if proof.total_tokens <= 0 or not proof.trace_id or not proof.thread_id:
        raise CapabilityError("model capability provenance is incomplete")
    expires = proof.proven_at + timedelta(days=30)
    with connection:
        connection.execute(
            """
            INSERT INTO model_capability_proofs (
                id, model, effort, thread_id, trace_id, schema_version, total_tokens,
                tool_version, schema_fingerprint, proven_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                proof.model,
                proof.effort,
                proof.thread_id,
                proof.trace_id,
                proof.total_tokens,
                proof.tool_version,
                proof.schema_fingerprint,
                proof.proven_at.isoformat(),
                expires.isoformat(),
            ),
        )


def enforce_model_proofs(
    connection: sqlite3.Connection,
    *,
    tool_version: str,
    schema_fingerprint_value: str,
) -> None:
    now = datetime.now(UTC).isoformat()
    rows = connection.execute(
        """
        SELECT model, effort FROM model_capability_proofs
        WHERE tool_version = ? AND schema_fingerprint = ? AND expires_at > ?
        """,
        (tool_version, schema_fingerprint_value, now),
    ).fetchall()
    observed = {(str(row[0]), str(row[1])) for row in rows}
    required = {("gpt-5.6-luna", "medium"), ("gpt-5.5", "high")}
    if not required.issubset(observed):
        raise CapabilityError("required model capability proof is missing or expired")

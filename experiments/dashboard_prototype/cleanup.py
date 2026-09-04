"""Exact-selector reconciliation and cleanup for disposable prototype telemetry."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from agent_introspection.config import AppConfig, load_config
from agent_introspection.source import ClickHouseClient
from experiments.dashboard_prototype.recurrence_execution import canonical_hash

NAMESPACE = "agent-introspection.dashboard-prototype.v1"
_EVENT_FAMILIES = {
    "attribution": "dashboard_prototype.attribution_snapshot.v1",
    "pipeline": "dashboard_prototype.pipeline_snapshot.v1",
    "recurrence": "dashboard_prototype.recurrence_snapshot.v1",
    "request": "dashboard_prototype.request_snapshot.v1",
    "task": "dashboard_prototype.task_operation.v1",
}
_REMOTE_IDENTITY_SQL = """
SELECT attributes_string['event.id'] AS event_id,
       any(attributes_string['dashboard.namespace']) AS remote_namespace,
       uniqExact(tuple(attributes_string['dashboard.namespace'],
                       attributes_string['event.name'],
                       attributes_string['dashboard.run_id_hash'])) AS immutable_tuple_count,
       count() AS row_count
FROM signoz_logs.distributed_logs_v2
WHERE attributes_string['event.name'] = {event_name:String}
  AND attributes_string['dashboard.run_id_hash'] = {run_id_hash:String}
  AND attributes_string['event.id'] IN {event_ids:Array(String)}
GROUP BY event_id
ORDER BY event_id
""".strip()
_REMOTE_DELETE_SQL = """
ALTER TABLE signoz_logs.logs_v2 ON CLUSTER cluster DELETE WHERE
  attributes_string['dashboard.namespace'] = {namespace:String}
  AND attributes_string['event.name'] = {event_name:String}
  AND attributes_string['dashboard.run_id_hash'] = {run_id_hash:String}
  AND attributes_string['event.id'] IN {event_ids:Array(String)}
SETTINGS mutations_sync = 2
""".strip()


class QueryClient(Protocol):
    """Execute one parameterized ClickHouse statement."""

    def query(
        self, sql: str, parameters: Mapping[str, str | int]
    ) -> Iterable[Mapping[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class CleanupSelector:
    """One exact experiment run selector, coalesced across retained attempts."""

    event_name: str
    event_ids: tuple[str, ...]
    manifest_names: tuple[str, ...]
    namespace: str
    run_id: str

    @property
    def run_id_hash(self) -> str:
        return canonical_hash(self.run_id)

    @property
    def selector_hash(self) -> str:
        return canonical_hash(
            {
                "event_ids": self.event_ids,
                "event_name": self.event_name,
                "namespace": self.namespace,
                "run_id": self.run_id,
            }
        )


class CleanupError(RuntimeError):
    """Reject an incomplete or inexact cleanup boundary."""


def load_cleanup_selectors(paths: Sequence[Path]) -> tuple[CleanupSelector, ...]:
    """Load and coalesce exact selectors from every supplied cleanup manifest."""
    grouped_ids: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    grouped_manifests: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    event_owner: dict[str, tuple[str, str, str]] = {}
    for path in sorted(paths):
        payload = json.loads(path.read_text())
        namespace = str(payload.get("namespace", ""))
        if namespace != NAMESPACE:
            raise CleanupError(f"unexpected cleanup namespace in {path}")
        attempts = payload.get("attempts")
        if not isinstance(attempts, list):
            raise CleanupError(f"cleanup attempts missing in {path}")
        for attempt in attempts:
            key, event_ids = _manifest_attempt(path, payload, namespace, attempt)
            grouped_manifests[key].add(path.name)
            for event_id in event_ids:
                owner = event_owner.setdefault(event_id, key)
                if owner != key:
                    raise CleanupError("one event ID is bound to multiple cleanup runs")
                grouped_ids[key].add(event_id)
    return tuple(
        CleanupSelector(
            namespace=key[0],
            run_id=key[1],
            event_name=key[2],
            event_ids=tuple(sorted(grouped_ids[key])),
            manifest_names=tuple(sorted(grouped_manifests[key])),
        )
        for key in sorted(grouped_manifests)
    )


def _manifest_attempt(
    path: Path,
    payload: Mapping[str, Any],
    namespace: str,
    attempt: object,
) -> tuple[tuple[str, str, str], tuple[str, ...]]:
    if not isinstance(attempt, dict):
        raise CleanupError(f"invalid cleanup attempt in {path}")
    embedded = attempt.get("cleanup_selector")
    embedded_selector = embedded if isinstance(embedded, dict) else {}
    run_id = str(attempt.get("run_id", embedded_selector.get("run_id", "")))
    if not run_id:
        raise CleanupError(f"cleanup run ID missing in {path}")
    embedded_namespace = str(embedded_selector.get("namespace", namespace))
    if embedded_namespace != namespace:
        raise CleanupError(f"cleanup namespace mismatch in {path}")
    event_name = str(payload.get("event_name", _event_family(path.name)))
    event_ids = _attempt_event_ids(attempt, embedded_selector)
    if len(event_ids) != len(set(event_ids)):
        raise CleanupError(f"duplicate event ID within one attempt in {path}")
    return (namespace, run_id, event_name), event_ids


def reconcile_selector(
    connection: sqlite3.Connection,
    client: QueryClient,
    selector: CleanupSelector,
) -> dict[str, Any]:
    """Reconcile one exact selector against immutable local and remote identities."""
    local = _local_rows(connection, selector)
    remote = _remote_rows(client, selector)
    expected = set(selector.event_ids)
    remote_ids = set(remote)
    return {
        "event_name": selector.event_name,
        "expected_event_count": len(expected),
        "local_delivered_count": len(local),
        "manifest_names": list(selector.manifest_names),
        "namespace": selector.namespace,
        "remote_duplicate_row_count": sum(
            max(0, row_count - 1) for row_count, _ in remote.values()
        ),
        "remote_namespace_omitted_count": sum(
            remote_namespace == "" for _, remote_namespace in remote.values()
        ),
        "remote_missing_event_count": len(expected - remote_ids),
        "remote_present_event_count": len(remote_ids),
        "run_id": selector.run_id,
        "selector_hash": selector.selector_hash,
    }


def execute_cleanup(
    *,
    config: AppConfig,
    manifest_paths: Sequence[Path],
    evidence_path: Path,
    apply: bool,
) -> dict[str, Any]:
    """Reconcile exact identities, delete remote telemetry, and retain immutable outbox proof."""
    selectors = load_cleanup_selectors(manifest_paths)
    connection = sqlite3.connect(config.database.path)
    connection.execute(f"PRAGMA busy_timeout = {int(config.database.busy_timeout_ms)}")
    client = ClickHouseClient(
        docker_context=config.signoz.docker_context,
        container=config.signoz.clickhouse_container,
    )
    manifest_names = [str(path) for path in sorted(manifest_paths)]
    unique_event_count = len(
        {event_id for selector in selectors for event_id in selector.event_ids}
    )
    selector_hashes = [selector.selector_hash for selector in selectors]
    try:
        current = [reconcile_selector(connection, client, selector) for selector in selectors]
        prior_summary = _prior_reconciliation_summary(
            evidence_path,
            manifest_names=manifest_names,
            selector_count=len(selectors),
            selector_hashes=selector_hashes,
            unique_event_count=unique_event_count,
        )
        initial_summary = prior_summary or _summarize_reconciliation(current)
        retained_metadata = _prior_cleanup_metadata(evidence_path)
        if apply:
            for selector in selectors:
                if selector.event_ids:
                    tuple(
                        client.query(
                            _REMOTE_DELETE_SQL,
                            _remote_parameters(selector, include_namespace=True),
                        )
                    )
            remote_after = {
                selector.selector_hash: _remote_rows(client, selector) for selector in selectors
            }
            remaining_remote = sum(len(rows) for rows in remote_after.values())
            if remaining_remote:
                raise CleanupError(
                    f"remote exact-selector cleanup left {remaining_remote} event identities"
                )
            local_retained_count = sum(
                len(_local_rows(connection, selector)) for selector in selectors
            )
            if local_retained_count != unique_event_count:
                raise CleanupError("immutable local outbox retention count mismatch")
        result = {
            "applied": apply,
            "cleanup_policy": (
                "exact namespace + exact run_id + exact event family + exact event_ids only"
            ),
            "manifest_paths": manifest_names,
            "namespace": NAMESPACE,
            "initial_reconciliation_summary": initial_summary,
            "pre_cleanup": current,
            "query_templates": {
                "reconciliation": _REMOTE_IDENTITY_SQL,
                "remote_delete": _REMOTE_DELETE_SQL,
            },
            "resume_state": {
                "prior_reconciliation_reused": prior_summary is not None,
                "remote_present_before_this_invocation": sum(
                    int(item["remote_present_event_count"]) for item in current
                ),
            },
            "selector_count": len(selectors),
            "selector_hashes": selector_hashes,
            "unique_event_count": unique_event_count,
            **retained_metadata,
        }
        if apply:
            result["post_cleanup"] = {
                "local_retained_event_count": unique_event_count,
                "local_retention_policy": "immutable otlp_outbox no-delete guard",
                "remote_event_count": 0,
            }
        evidence_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        return result
    finally:
        connection.close()


def _event_family(manifest_name: str) -> str:
    for prefix, event_name in _EVENT_FAMILIES.items():
        if manifest_name.startswith(prefix):
            return event_name
    raise CleanupError(f"unknown cleanup manifest family: {manifest_name}")


def _attempt_event_ids(
    attempt: Mapping[str, Any], embedded_selector: Mapping[str, Any]
) -> tuple[str, ...]:
    embedded_ids = embedded_selector.get("event_ids")
    if isinstance(embedded_ids, list):
        values = embedded_ids
    else:
        primitive = attempt.get("primitive_event_ids", [])
        result = attempt.get("result_event_ids", [])
        if not isinstance(primitive, list) or not isinstance(result, list):
            raise CleanupError("cleanup event ID lists are invalid")
        values = primitive + result
    if not all(isinstance(value, str) and value for value in values):
        raise CleanupError("cleanup event IDs must be non-empty strings")
    return tuple(values)


def _local_rows(connection: sqlite3.Connection, selector: CleanupSelector) -> dict[str, str]:
    if not selector.event_ids:
        return {}
    placeholders = ",".join("?" for _ in selector.event_ids)
    rows = connection.execute(
        f"SELECT event_id, payload_json, status FROM otlp_outbox "
        f"WHERE event_id IN ({placeholders})",
        selector.event_ids,
    ).fetchall()
    if len(rows) != len(selector.event_ids):
        raise CleanupError(f"local outbox is missing exact IDs for {selector.run_id}")
    observed: dict[str, str] = {}
    for event_id, payload_json, status in rows:
        payload = json.loads(str(payload_json))
        expected = (
            payload.get("event.id") == event_id
            and payload.get("event.name") == selector.event_name
            and payload.get("event.scope") == selector.namespace
            and payload.get("dashboard.run_id_hash") == selector.run_id_hash
            and status == "delivered"
        )
        if not expected:
            raise CleanupError(f"local immutable tuple mismatch for {selector.run_id}")
        observed[str(event_id)] = str(status)
    return observed


def _remote_parameters(selector: CleanupSelector, *, include_namespace: bool) -> dict[str, str]:
    parameters = {
        "event_ids": "['" + "','".join(selector.event_ids) + "']",
        "event_name": selector.event_name,
        "run_id_hash": selector.run_id_hash,
    }
    if include_namespace:
        parameters["namespace"] = selector.namespace
    return parameters


def _remote_rows(client: QueryClient, selector: CleanupSelector) -> dict[str, tuple[int, str]]:
    if not selector.event_ids:
        return {}
    observed: dict[str, tuple[int, str]] = {}
    for row in client.query(
        _REMOTE_IDENTITY_SQL, _remote_parameters(selector, include_namespace=False)
    ):
        event_id = str(row.get("event_id", ""))
        if event_id not in selector.event_ids:
            raise CleanupError("remote query returned an event outside the exact selector")
        if int(row.get("immutable_tuple_count", 0)) != 1:
            raise CleanupError(f"remote immutable tuple conflict for {selector.run_id}")
        remote_namespace = str(row.get("remote_namespace", ""))
        if remote_namespace not in ("", selector.namespace):
            raise CleanupError(f"remote namespace mismatch for {selector.run_id}")
        observed[event_id] = (int(row.get("row_count", 0)), remote_namespace)
    return observed


def _prior_cleanup_metadata(evidence_path: Path) -> dict[str, Mapping[str, Any]]:
    if not evidence_path.exists():
        return {}
    payload = json.loads(evidence_path.read_text())
    retained: dict[str, Mapping[str, Any]] = {}
    for field in ("cleanup_incident", "initial_reconciliation_provenance"):
        value = payload.get(field)
        if value is not None:
            if not isinstance(value, dict):
                raise CleanupError(f"prior cleanup {field} is invalid")
            retained[field] = value
    return retained


def _summarize_reconciliation(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    fields = (
        "expected_event_count",
        "local_delivered_count",
        "remote_present_event_count",
        "remote_missing_event_count",
        "remote_duplicate_row_count",
        "remote_namespace_omitted_count",
    )
    return {field: sum(int(row[field]) for row in rows) for field in fields}


def _prior_reconciliation_summary(
    evidence_path: Path,
    *,
    manifest_names: Sequence[str],
    selector_count: int,
    selector_hashes: Sequence[str],
    unique_event_count: int,
) -> dict[str, int] | None:
    if not evidence_path.exists():
        return None
    payload = json.loads(evidence_path.read_text())
    expected = (
        payload.get("manifest_paths") == list(manifest_names)
        and payload.get("selector_count") == selector_count
        and payload.get("selector_hashes") == list(selector_hashes)
        and payload.get("unique_event_count") == unique_event_count
    )
    if not expected:
        raise CleanupError("prior cleanup reconciliation does not match exact selectors")
    summary = payload.get("initial_reconciliation_summary")
    if summary is None:
        return None
    if not isinstance(summary, dict):
        raise CleanupError("prior cleanup reconciliation summary is invalid")
    required = {
        "expected_event_count",
        "local_delivered_count",
        "remote_present_event_count",
        "remote_missing_event_count",
        "remote_duplicate_row_count",
        "remote_namespace_omitted_count",
    }
    if set(summary) != required or not all(
        isinstance(summary[field], int) and summary[field] >= 0 for field in required
    ):
        raise CleanupError("prior cleanup reconciliation summary is invalid")
    return {field: int(summary[field]) for field in required}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", action="append", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    result = execute_cleanup(
        config=load_config(),
        manifest_paths=args.manifest,
        evidence_path=args.evidence,
        apply=bool(args.apply),
    )
    print(
        json.dumps(
            {
                "applied": result["applied"],
                "selector_count": result["selector_count"],
                "unique_event_count": result["unique_event_count"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import struct
import time
import uuid
from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from agent_introspection.database import connect_database
from agent_introspection.pipeline_delivery import (
    capture_final_drain,
    enqueue_final_drain_projection,
    query_delivery_detail,
)
from agent_introspection.telemetry import DerivedEvent, enqueue_events


class DeliveryQuery:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def query(self, sql: str, parameters: Mapping[str, str | int]) -> list[dict[str, Any]]:
        return [
            row
            for row in self.rows
            if row["strings"]["delivery.scan_run_id"] == parameters["scan_run_id"]
        ]


def _remote_rows(connection: Any) -> list[dict[str, Any]]:
    rows = []
    for (payload,) in connection.execute("SELECT payload_json FROM otlp_outbox"):
        values = json.loads(payload)
        if values.get("event.scope") != "pipeline-delivery":
            continue
        rows.append(
            {
                "timestamp": values["timestamp_ns"],
                "body": json.dumps(values, separators=(",", ":")),
                "strings": {key: value for key, value in values.items() if isinstance(value, str)},
                "number_bits": {
                    key: str(int.from_bytes(struct.pack(">d", float(value)), "big"))
                    for key, value in values.items()
                    if key != "timestamp_ns"
                    and isinstance(value, (int, float))
                    and not isinstance(value, bool)
                },
                "booleans": {
                    key: value for key, value in values.items() if isinstance(value, bool)
                },
            }
        )
    return rows


def _scan(connection: Any) -> str:
    identity = str(uuid.uuid4())
    connection.execute(
        "INSERT INTO scan_runs(id, status, started_at) VALUES (?, 'running', ?)",
        (identity, datetime.now(UTC).isoformat()),
    )
    connection.commit()
    return identity


def test_delivery_metrics_conserve_complete_population_despite_caps_duplicates_and_recovery(
    tmp_path: Path,
) -> None:
    connection = connect_database(tmp_path / "delivery.sqlite3")
    events = [
        DerivedEvent(
            "operational", str(index), 1, 1, "introspection.test.delivery", {}, time.time_ns()
        )
        for index in range(60)
    ]
    enqueue_events(connection, events)
    scan = _scan(connection)
    success = MagicMock()
    success.__enter__.return_value.status = 200
    with patch("urllib.request.urlopen", side_effect=[success, TimeoutError]):
        captured = capture_final_drain(
            connection,
            scan_run_id=scan,
            endpoint="http://localhost:4318/v1/logs",
            limit=50,
            max_batches=2,
        )
    completed = time.time_ns()
    pending = connection.execute(
        "SELECT COUNT(*) FROM otlp_outbox WHERE status = 'pending'"
    ).fetchone()[0]
    selected = connection.execute("SELECT COUNT(*) FROM otlp_outbox").fetchone()[0]
    enqueue_final_drain_projection(connection, captured)
    remote = _remote_rows(connection)
    panel = query_delivery_detail(
        DeliveryQuery(remote * 2), scan_run_id=scan, completed_at_ns=completed
    )
    assert panel["state"] == "Data"
    assert len(panel["rows"]) == 50
    rate = next(metric for metric in panel["metrics"] if metric["unit"] == "percent")
    assert (rate["numerator"], rate["denominator"], rate["value"]) == (
        pending,
        selected,
        100 * pending / selected,
    )

    incomplete = [
        row
        for row in remote
        if row["strings"]["event.name"] != "introspection.pipeline.delivery.attempt"
    ]
    assert (
        query_delivery_detail(
            DeliveryQuery(incomplete), scan_run_id=scan, completed_at_ns=completed
        )["state"]
        == "Integrity failure"
    )
    divergent = deepcopy(remote)
    divergent[0]["strings"]["delivery.destination"] = "changed"
    assert (
        query_delivery_detail(
            DeliveryQuery(remote + divergent), scan_run_id=scan, completed_at_ns=completed
        )["state"]
        == "Integrity failure"
    )

    # Recovery drains prior measurement events without creating an observation recursion.
    connection.execute(
        "UPDATE otlp_outbox SET next_attempt_at = created_at WHERE status = 'pending'"
    )
    recovery_scan = _scan(connection)
    with patch("urllib.request.urlopen", return_value=success):
        recovery = capture_final_drain(
            connection, scan_run_id=recovery_scan, endpoint="http://localhost:4318/v1/logs"
        )
    assert recovery.selected_events == pending
    assert recovery.delivered_events == pending
    assert recovery.failed_events == recovery.pending_events == 0
    enqueue_final_drain_projection(connection, recovery)
    recovered = query_delivery_detail(
        DeliveryQuery(_remote_rows(connection)),
        scan_run_id=recovery_scan,
        completed_at_ns=time.time_ns(),
    )
    assert recovered["state"] == "Data"
    recovery_rate = next(metric for metric in recovered["metrics"] if metric["unit"] == "percent")
    assert (recovery_rate["numerator"], recovery_rate["denominator"]) == (0, pending)
    connection.close()


def test_delivery_distinguishes_missing_projection_from_query_failure() -> None:
    class FailedQuery:
        def query(self, sql: str, parameters: Mapping[str, str | int]) -> list[dict[str, Any]]:
            raise ValueError("invalid remote response")

    assert (
        query_delivery_detail(DeliveryQuery([]), scan_run_id="scan", completed_at_ns=100)["state"]
        == "Unavailable"
    )
    assert (
        query_delivery_detail(FailedQuery(), scan_run_id="scan", completed_at_ns=100)["state"]
        == "Query/system error"
    )

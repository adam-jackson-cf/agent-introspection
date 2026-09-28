"""Remote-only Pipeline report entrypoint for the companion server."""

from __future__ import annotations

import argparse
import json
import os
import signal
from datetime import UTC, datetime

from agent_introspection.config import load_config
from agent_introspection.pipeline_attribution import query_attribution
from agent_introspection.pipeline_contracts import PipelineReport
from agent_introspection.pipeline_projection import (
    PipelineWindow,
    SnapshotCache,
    project_pipeline,
)
from agent_introspection.pipeline_runtime import implementation_fingerprint
from agent_introspection.source import (
    ClickHouseClient,
    DashboardCancellation,
    DashboardClickHouseClient,
)

_PANEL_IDS = (
    "p1-snapshot",
    "p2-outcomes",
    "p2-freshness",
    "p3-duration",
    "p3-rows",
    "p3-throughput",
    "p4-source-lag",
    "p5-correlation",
    "p6-delay",
    "p7-coverage",
    "p8-diagnostics",
    "p9-transitions",
    "p9-evidence",
    "p10-snapshot",
    "p10-delivery-detail",
    "p11-integrity",
    "p12-ledger",
    "scan-evidence",
)


def _epoch_ns(value: datetime) -> int:
    delta = value.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


def _utc_millis(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include UTC offset")
    return parsed.astimezone(UTC)


def _request_deadline_from_environment() -> int | None:
    value = os.environ.get("PIPELINE_REQUEST_DEADLINE_UNIX_MS")
    if value is None:
        return None
    try:
        deadline_unix_ms = int(value)
    except ValueError as exc:
        raise RuntimeError("PIPELINE_REQUEST_DEADLINE_UNIX_MS must be an integer") from exc
    if deadline_unix_ms <= 0:
        raise RuntimeError("PIPELINE_REQUEST_DEADLINE_UNIX_MS must be positive")
    return deadline_unix_ms


def _cancel_dashboard_request(_signal_number: int, _frame: object) -> None:
    raise DashboardCancellation("dashboard request cancelled by SIGTERM")


def query_report(
    *,
    start: datetime,
    end: datetime,
    evaluated_at: datetime | None = None,
    deadline_unix_ms: int | None = None,
) -> PipelineReport:
    evaluated = (evaluated_at or datetime.now(UTC)).astimezone(UTC)
    config = load_config()
    client = (
        DashboardClickHouseClient(
            docker_context=config.signoz.docker_context,
            container=config.signoz.clickhouse_container,
            deadline_unix_ms=deadline_unix_ms,
        )
        if deadline_unix_ms is not None
        else ClickHouseClient(
            docker_context=config.signoz.docker_context,
            container=config.signoz.clickhouse_container,
        )
    )
    # Apply the same millisecond bounds that the report exposes to its consumer.
    start_ns = _epoch_ns(start) // 1_000_000 * 1_000_000
    end_ns = _epoch_ns(end) // 1_000_000 * 1_000_000
    evaluated_ns = _epoch_ns(evaluated) // 1_000_000 * 1_000_000
    if start_ns >= end_ns:
        raise ValueError("start must be before end at millisecond precision")
    measurement_start = config.pipeline.measurement_start
    measurement_start_ns = (
        _epoch_ns(measurement_start) // 1_000_000 * 1_000_000
        if measurement_start is not None
        else None
    )
    window = PipelineWindow(start_ns, end_ns, evaluated_ns)
    snapshot_cache: SnapshotCache = {}
    attribution, attribution_scan_ids = query_attribution(
        client,
        window=window,
        measurement_start_ns=measurement_start_ns,
        snapshot_cache=snapshot_cache,
    )
    panels, deployments = project_pipeline(
        client,
        window=window,
        attribution_scan_ids=attribution_scan_ids,
        measurement_start_ns=measurement_start_ns,
        snapshot_cache=snapshot_cache,
    )
    panels.update(attribution)
    if measurement_start_ns is not None and start_ns < measurement_start_ns < end_ns:
        reason = f"fresh measurement cohort starts at {measurement_start_ns} ns"
        for panel in panels.values():
            panel["reasons"] = [*panel["reasons"], reason]
    missing = set(_PANEL_IDS) - set(panels)
    if missing:
        raise RuntimeError(f"pipeline projection omitted panels: {sorted(missing)}")
    return {
        "start": _utc_millis(start),
        "end": _utc_millis(end),
        "evaluatedAt": _utc_millis(evaluated),
        "implementation": {
            "calculationId": "agent-introspection.pipeline-dashboard",
            "calculationSha256": implementation_fingerprint(),
            "deployments": deployments,
        },
        "panels": panels,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=_parse, required=True)
    parser.add_argument("--end", type=_parse, required=True)
    arguments = parser.parse_args()
    deadline_unix_ms = _request_deadline_from_environment()
    previous_sigterm_handler = signal.signal(signal.SIGTERM, _cancel_dashboard_request)
    try:
        report = query_report(
            start=arguments.start,
            end=arguments.end,
            deadline_unix_ms=deadline_unix_ms,
        )
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm_handler)
    print(json.dumps(report, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()

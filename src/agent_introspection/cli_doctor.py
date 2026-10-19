"""The doctor command of the Agent Introspection CLI, wired to this machine."""

from __future__ import annotations

import argparse
import os
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from agent_introspection import (
    dashboard_service,
    doctor,
    doctor_harness,
    facts,
    producers,
    schedule,
    signoz_detect,
)


def _drift_report(
    run: facts.SqlRunner, saved_dir: Path | None, services: Sequence[str]
) -> dict[str, Any]:
    return producers.drift_report(run, saved_dir or producers.FINGERPRINT_DIR, services)


def default_probes() -> doctor.Probes:
    """Return the probes that touch this machine."""
    return doctor.Probes(
        connect=facts.runner,
        tcp_open=doctor.tcp_open,
        post_json=doctor.post_json,
        http_get=doctor.http_get,
        schedule_status=schedule.schedule_status,
        spool_sync_status=schedule.spool_sync_status,
        dashboard_status=dashboard_service.status,
        detect=signoz_detect.detect,
        drift_report=_drift_report,
        harness=doctor_harness.HarnessProbes(
            which=shutil.which,
            version=doctor_harness.installed_version,
            environ=os.environ,
            home=Path.home(),
        ),
    )


def add_doctor_parser(commands: Any) -> None:
    parser = commands.add_parser(
        "doctor",
        help="read-only checks of SigNoz, the collector, each enabled harness, the spool, "
        "the schedule, prompt labels, and the dashboard",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="also POST an empty OTLP/HTTP logs request to the hooks endpoint (stores nothing)",
    )
    parser.add_argument(
        "--drift",
        action="store_true",
        help="also compare each enabled producer's telemetry with its saved fingerprint",
    )


def doctor_command(args: argparse.Namespace) -> dict[str, Any]:
    options = doctor.Options(
        config_path=Path(args.config) if args.config is not None else None,
        live=args.live,
        drift=args.drift,
    )
    return doctor.report(options, default_probes())

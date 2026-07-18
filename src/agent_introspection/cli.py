"""Structured command-line interface for Agent Introspection."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any, NoReturn

from agent_introspection.capabilities import (
    CapabilityError,
    approve_schema,
    discover_source_schema,
    enforce_approved_schema,
    ensure_health,
    verify_network_perimeter,
)
from agent_introspection.config import ConfigurationError, load_config
from agent_introspection.dashboard import load_dashboard, verify_dashboard
from agent_introspection.database import (
    DatabaseError,
    backup_database,
    connect_database,
    integrity_check,
    manual_vacuum,
    quick_check,
    restore_database,
    weekly_maintenance,
)
from agent_introspection.generations import activate_generation, stage_generation
from agent_introspection.proposals import (
    ProposalInput,
    ProposalState,
    create_proposal,
    transition_proposal,
)
from agent_introspection.review import (
    create_review_session,
    import_model_output,
    validate_model_output,
)
from agent_introspection.scan import run_scan
from agent_introspection.scheduler import (
    completed_in_current_slot,
    install_schedule,
    launch_agent_payload,
    remove_schedule,
    scan_lease,
    schedule_status,
)
from agent_introspection.source import ClickHouseClient, SourceError
from agent_introspection.telemetry import (
    drain_outbox,
    enqueue_observation_reconciliation,
    plan_observation_reconciliation,
    remote_observation_event_ids,
)

EXIT_CONFIG = 10
EXIT_CAPABILITY = 20
EXIT_SOURCE = 30
EXIT_DATABASE = 40
EXIT_VALIDATION = 50
EXIT_CONFLICT = 60
EXIT_INTERNAL = 70


def _emit(value: object) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _diagnostic(message: str) -> None:
    print(message, file=sys.stderr)


def _read_json(source: str) -> dict[str, Any]:
    try:
        text = sys.stdin.read() if source == "-" else Path(source).read_text()
        value = json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("input JSON is unreadable or invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("input JSON must contain an object")
    return value


def _config_path(value: str | None) -> Path | None:
    return Path(value) if value is not None else None


def _open(args: argparse.Namespace) -> tuple[Any, sqlite3.Connection]:
    config = load_config(_config_path(args.config))
    connection = connect_database(
        config.database.path,
        busy_timeout_ms=config.database.busy_timeout_ms,
    )
    return config, connection


def _client(config: Any) -> ClickHouseClient:
    return ClickHouseClient(
        docker_context=config.signoz.docker_context,
        container=config.signoz.clickhouse_container,
    )


def _doctor(args: argparse.Namespace) -> dict[str, Any]:
    config, connection = _open(args)
    try:
        health = ensure_health(
            health_url=config.signoz.health_url,
            compose_directory=config.signoz.compose_directory,
            docker_context=config.signoz.docker_context,
        )
        network = verify_network_perimeter(docker_context=config.signoz.docker_context)
        inventory = discover_source_schema(_client(config))
        fingerprint = (
            approve_schema(connection, inventory, approved_by="doctor")
            if args.approve_schema
            else enforce_approved_schema(connection, inventory)
        )
        return {
            "status": "ok",
            "health": health,
            "network": network,
            "schema_fingerprint": fingerprint,
            "schema_approved": True,
        }
    finally:
        connection.close()


def _health(args: argparse.Namespace) -> dict[str, Any]:
    config = load_config(_config_path(args.config))
    health = ensure_health(
        health_url=config.signoz.health_url,
        compose_directory=config.signoz.compose_directory,
        docker_context=config.signoz.docker_context,
    )
    network = verify_network_perimeter(docker_context=config.signoz.docker_context)
    return {"status": "ok", "health": health, "network": network}


def _scan(args: argparse.Namespace) -> dict[str, Any]:
    config, connection = _open(args)
    try:
        if args.scheduled:
            now = datetime.now(UTC)
            completed = completed_in_current_slot(
                connection,
                now=now,
                interval_seconds=config.scheduler.interval_seconds,
            )
            if completed is not None:
                return {
                    "status": "already_completed_in_slot",
                    "slot_start": completed.slot_start,
                    "interval_seconds": config.scheduler.interval_seconds,
                    "qualifying_run_id": completed.run_id,
                    "qualifying_run_started_at": completed.started_at,
                }
        return run_scan(connection, config)
    finally:
        connection.close()


def _analysis_generation(args: argparse.Namespace) -> dict[str, Any]:
    config, connection = _open(args)
    try:
        quick_check(connection)
        verify_network_perimeter(docker_context=config.signoz.docker_context)
        source = _client(config)
        fingerprint = enforce_approved_schema(connection, discover_source_schema(source))
        with scan_lease(
            connection,
            duration=timedelta(seconds=config.scheduler.lease_seconds),
        ):
            if args.analysis_generation_command == "stage":
                staged = stage_generation(
                    connection,
                    source_contract_fingerprint=fingerprint,
                )
                return {
                    "status": "staged",
                    "generation_id": staged.generation_id,
                    "ordinal": staged.ordinal,
                    "window_start_ns": staged.window_start_ns,
                    "window_end_ns": staged.window_end_ns,
                    "semantic_hash": staged.semantic_hash,
                    "projection_events": len(staged.projection_event_ids),
                }
            activated = activate_generation(
                connection,
                generation_id=args.generation_id,
                client=source,
                endpoint=f"{config.signoz.otlp_http_endpoint.rstrip('/')}/v1/logs",
            )
            return {
                "status": "activated",
                "generation_id": activated.generation_id,
                "activation_event_id": activated.activation_event_id,
                "projection_events": activated.projection_count,
            }
    finally:
        connection.close()


def _candidates_export(args: argparse.Namespace) -> dict[str, Any]:
    _config, connection = _open(args)
    try:
        if args.kind == "classification":
            rows = connection.execute(
                """
                SELECT o.id, o.category, o.membership_explanation, o.attributes_json
                FROM observations o
                LEFT JOIN semantic_classifications c ON c.candidate_id = o.id
                WHERE c.id IS NULL ORDER BY o.occurred_at_ns, o.id LIMIT 10
                """
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT f.id, f.category, f.trend_state, f.fingerprint
                FROM findings f
                LEFT JOIN proposals p ON p.finding_id = f.id
                WHERE f.trend_state = 'actionable' AND p.id IS NULL
                ORDER BY f.last_seen_ns, f.id LIMIT 1
                """
            ).fetchall()
        if not rows:
            return {"status": "no_candidates", "kind": args.kind}
        candidates = [{"id": str(row[0]), "fields": [value for value in row[1:]]} for row in rows]
        envelope = create_review_session(
            connection,
            kind=args.kind,
            candidates=candidates,
            reserved_model_budget=args.reserved_model_budget,
            batch_id=args.batch_id,
        )
        return {"status": "exported", "review": envelope.as_dict()}
    finally:
        connection.close()


def _classification_import(args: argparse.Namespace) -> dict[str, Any]:
    document = _read_json(args.input_json)
    provenance = document.pop("provenance", None)
    if not isinstance(provenance, dict):
        raise ValueError("classification import requires provenance")
    _config, connection = _open(args)
    try:
        import_model_output(connection, document, provenance=provenance)
        return {"status": "imported", "session_id": document["session_id"]}
    finally:
        connection.close()


def _proposal_create(args: argparse.Namespace) -> dict[str, Any]:
    document = _read_json(args.input_json)
    provenance = document.pop("provenance", None)
    if not isinstance(provenance, dict):
        raise ValueError("proposal creation requires provenance")
    _config, connection = _open(args)
    try:
        _kind, results = validate_model_output(connection, document, provenance=provenance)
        if _kind != "proposal":
            raise ValueError("proposal creation requires a proposal review session")
        proposal_inputs: list[ProposalInput] = []
        for result in results:
            payload = result.get("proposal")
            if not isinstance(payload, dict):
                raise ValueError("proposal result requires a proposal object")
            proposal_input = ProposalInput(**payload)
            finding = connection.execute(
                "SELECT trend_state FROM findings WHERE id = ?", (proposal_input.finding_id,)
            ).fetchone()
            if finding is None or finding[0] != "actionable":
                raise ValueError("only actionable findings can produce proposals")
            proposal_inputs.append(proposal_input)
        import_model_output(connection, document, provenance=provenance)
        proposal_ids = [create_proposal(connection, value) for value in proposal_inputs]
        return {"status": "created", "proposal_ids": proposal_ids}
    finally:
        connection.close()


def _proposal_list(args: argparse.Namespace) -> dict[str, Any]:
    _config, connection = _open(args)
    try:
        rows = connection.execute(
            "SELECT id, finding_id, state, created_at, updated_at "
            "FROM proposals ORDER BY created_at"
        ).fetchall()
        return {
            "proposals": [
                {
                    "id": row[0],
                    "finding_id": row[1],
                    "state": row[2],
                    "created_at": row[3],
                    "updated_at": row[4],
                }
                for row in rows
            ]
        }
    finally:
        connection.close()


def _proposal_show(args: argparse.Namespace) -> dict[str, Any]:
    _config, connection = _open(args)
    try:
        row = connection.execute(
            "SELECT id, finding_id, state, payload_json, entity_version "
            "FROM proposals WHERE id = ?",
            (args.proposal_id,),
        ).fetchone()
        if row is None:
            raise KeyError(args.proposal_id)
        events = connection.execute(
            """
            SELECT sequence, event_type, payload_json, created_at
            FROM proposal_events WHERE proposal_id = ? ORDER BY sequence
            """,
            (args.proposal_id,),
        ).fetchall()
        return {
            "id": row[0],
            "finding_id": row[1],
            "state": row[2],
            "proposal": json.loads(row[3]),
            "entity_version": row[4],
            "events": [
                {
                    "sequence": event[0],
                    "event_type": event[1],
                    "payload": json.loads(event[2]),
                    "created_at": event[3],
                }
                for event in events
            ],
        }
    finally:
        connection.close()


def _proposal_decide(args: argparse.Namespace) -> dict[str, Any]:
    target = ProposalState.APPROVED if args.decision == "approve" else ProposalState.REJECTED
    _config, connection = _open(args)
    try:
        transition_proposal(
            connection,
            args.proposal_id,
            target,
            actor=args.actor,
            evidence={"decision": args.decision, "reason": args.reason},
        )
        return {"status": target, "proposal_id": args.proposal_id}
    finally:
        connection.close()


def _proposal_mark_applied(args: argparse.Namespace) -> dict[str, Any]:
    evidence = _read_json(args.input_json)
    if not evidence.get("validation"):
        raise ValueError("mark-applied requires validation evidence")
    _config, connection = _open(args)
    try:
        state = connection.execute(
            "SELECT state FROM proposals WHERE id = ?", (args.proposal_id,)
        ).fetchone()
        if state is None:
            raise KeyError(args.proposal_id)
        if state[0] == ProposalState.APPROVED:
            transition_proposal(
                connection,
                args.proposal_id,
                ProposalState.APPLYING,
                actor=args.actor,
                evidence={"request": "mark-applied"},
                explicit_application_request=True,
            )
        transition_proposal(
            connection,
            args.proposal_id,
            ProposalState.APPLIED,
            actor=args.actor,
            evidence=evidence,
        )
        return {"status": "applied", "proposal_id": args.proposal_id}
    finally:
        connection.close()


def _drain_outbox_to_idle(connection: sqlite3.Connection, *, endpoint: str) -> dict[str, int]:
    delivered = 0
    pending = 0
    while True:
        result = drain_outbox(connection, endpoint=endpoint, limit=500)
        delivered += result["delivered"]
        pending = result["pending"]
        if result["selected"] == 0 or result["delivered"] == 0:
            return {"telemetry_delivered": delivered, "telemetry_pending": pending}


def _telemetry_command(args: argparse.Namespace) -> dict[str, Any]:
    config, connection = _open(args)
    try:
        endpoint = f"{config.signoz.otlp_http_endpoint.rstrip('/')}/v1/logs"
        if args.telemetry_command == "drain":
            return drain_outbox(connection, endpoint=endpoint, limit=args.limit)
        verify_network_perimeter(docker_context=config.signoz.docker_context)
        client = _client(config)
        enforce_approved_schema(connection, discover_source_schema(client))
        with scan_lease(
            connection,
            duration=timedelta(seconds=config.scheduler.lease_seconds),
        ):
            plan = plan_observation_reconciliation(
                connection,
                scan_run_ids=args.scan_run_id,
            )
            remote_event_ids = remote_observation_event_ids(client, plan.events)
            result = enqueue_observation_reconciliation(
                connection,
                plan,
                remote_event_ids=remote_event_ids,
            )
        return {
            "status": "reconciled",
            **result,
            **_drain_outbox_to_idle(connection, endpoint=endpoint),
        }
    finally:
        connection.close()


def _dashboard_verify(args: argparse.Namespace) -> dict[str, Any]:
    resource = files("agent_introspection").joinpath("assets/agent-introspection.json")
    with as_file(resource) as path:
        document = load_dashboard(path)
    issues = verify_dashboard(document)
    if issues:
        raise ValueError("; ".join(issues))
    return {
        "status": "verified",
        "uuid": document["uuid"],
        "schema_version": document["schemaVersion"],
        "panel_count": len(document["widgets"]),
    }


def _db_command(args: argparse.Namespace) -> dict[str, Any]:
    config, connection = _open(args)
    try:
        if args.db_command == "check":
            return {"quick_check": quick_check(connection)}
        if args.db_command == "backup":
            destination = (
                Path(args.destination)
                if args.destination
                else (
                    config.database.path.parent
                    / "backups"
                    / f"manual-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.sqlite3"
                )
            )
            return {"backup_path": str(backup_database(connection, destination))}
        if args.db_command == "maintenance":
            maintenance_result = weekly_maintenance(connection, config.database.path)
            vacuum = manual_vacuum(connection, config.database.path) if args.vacuum else None
            return {
                "integrity_check": maintenance_result.integrity_result,
                "backup_path": str(maintenance_result.backup_path),
                "vacuum": (
                    {
                        "free_page_ratio": vacuum.free_page_ratio,
                        "vacuumed": vacuum.vacuumed,
                        "backup_path": str(vacuum.backup_path) if vacuum.backup_path else None,
                    }
                    if vacuum
                    else None
                ),
            }
        connection.close()
        restore_result = restore_database(
            config.database.path,
            Path(args.backup_path),
            busy_timeout_ms=config.database.busy_timeout_ms,
        )
        verified = connect_database(config.database.path)
        try:
            checked = integrity_check(verified)
        finally:
            verified.close()
        return {
            "database_path": str(restore_result.database_path),
            "safety_backup_path": (
                str(restore_result.safety_backup_path)
                if restore_result.safety_backup_path
                else None
            ),
            "integrity_check": checked,
        }
    finally:
        if connection:
            connection.close()


def _schedule_command(args: argparse.Namespace) -> dict[str, Any]:
    config = load_config(_config_path(args.config))
    if args.schedule_command == "status":
        return schedule_status()
    if args.schedule_command == "remove":
        return {"removed": remove_schedule()}
    executable = Path(shutil.which("agent-introspection") or sys.argv[0]).resolve()
    config_path = (
        _config_path(args.config) or Path("~/.config/agent-introspection/config.toml").expanduser()
    )
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        document = (
            "[database]\n"
            f'path = "{config.database.path}"\n'
            f"busy_timeout_ms = {config.database.busy_timeout_ms}\n\n"
            "[signoz]\n"
            f'health_url = "{config.signoz.health_url}"\n'
            f'otlp_http_endpoint = "{config.signoz.otlp_http_endpoint}"\n'
            f'compose_directory = "{config.signoz.compose_directory}"\n'
            f'clickhouse_container = "{config.signoz.clickhouse_container}"\n'
            f'collector_container = "{config.signoz.collector_container}"\n'
            f'docker_context = "{config.signoz.docker_context}"\n'
            f'docker_host = "{config.signoz.docker_host}"\n\n'
            "[scheduler]\n"
            f'timezone = "{config.scheduler.timezone}"\n'
            f"interval_seconds = {config.scheduler.interval_seconds}\n"
            f"lease_seconds = {config.scheduler.lease_seconds}\n"
        )
        descriptor = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(document)
    payload = launch_agent_payload(
        executable=executable,
        config_path=config_path,
        working_directory=Path.cwd(),
        docker_host=config.signoz.docker_host,
        log_directory=Path("~/.local/state/agent-introspection").expanduser(),
        interval_seconds=config.scheduler.interval_seconds,
        timezone=config.scheduler.timezone,
    )
    destination = install_schedule(payload)
    return {"installed": True, "path": str(destination), **schedule_status()}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-introspection")
    parser.add_argument("--config", default=os.environ.get("AGENT_INTROSPECTION_CONFIG"))
    commands = parser.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser("doctor")
    doctor.add_argument("--approve-schema", action="store_true")
    commands.add_parser("health")
    scan = commands.add_parser("scan")
    scan.add_argument("--scheduled", action="store_true")

    generation = commands.add_parser("analysis-generation").add_subparsers(
        dest="analysis_generation_command", required=True
    )
    generation.add_parser("stage")
    activate = generation.add_parser("activate")
    activate.add_argument("generation_id")

    candidates = commands.add_parser("candidates").add_subparsers(
        dest="candidates_command", required=True
    )
    export = candidates.add_parser("export")
    export.add_argument("--kind", choices=("classification", "proposal"), default="classification")
    export.add_argument("--batch-id")
    export.add_argument("--reserved-model-budget", type=int, required=True)

    classification = commands.add_parser("classification").add_subparsers(
        dest="classification_command", required=True
    )
    classification_import = classification.add_parser("import")
    classification_import.add_argument("--input-json", required=True)

    proposal = commands.add_parser("proposal").add_subparsers(
        dest="proposal_command", required=True
    )
    create = proposal.add_parser("create")
    create.add_argument("--input-json", required=True)
    proposal.add_parser("list")
    show = proposal.add_parser("show")
    show.add_argument("proposal_id")
    decide = proposal.add_parser("decide")
    decide.add_argument("proposal_id")
    decide.add_argument("decision", choices=("approve", "reject"))
    decide.add_argument("--actor", required=True)
    decide.add_argument("--reason", required=True)
    applied = proposal.add_parser("mark-applied")
    applied.add_argument("proposal_id")
    applied.add_argument("--actor", required=True)
    applied.add_argument("--input-json", required=True)

    telemetry = commands.add_parser("telemetry").add_subparsers(
        dest="telemetry_command", required=True
    )
    drain = telemetry.add_parser("drain")
    drain.add_argument("--limit", type=int, default=100)
    reconcile = telemetry.add_parser("reconcile-observations")
    reconcile.add_argument("--scan-run-id", action="append", required=True)

    dashboard = commands.add_parser("dashboard").add_subparsers(
        dest="dashboard_command", required=True
    )
    dashboard.add_parser("verify")

    db = commands.add_parser("db").add_subparsers(dest="db_command", required=True)
    db.add_parser("check")
    backup = db.add_parser("backup")
    backup.add_argument("--destination")
    maintenance = db.add_parser("maintenance")
    maintenance.add_argument("--vacuum", action="store_true")
    restore = db.add_parser("restore")
    restore.add_argument("backup_path")

    schedule = commands.add_parser("schedule").add_subparsers(
        dest="schedule_command", required=True
    )
    schedule.add_parser("install")
    schedule.add_parser("status")
    schedule.add_parser("remove")
    return parser


def _dispatch(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "doctor":
        return _doctor(args)
    if args.command == "health":
        return _health(args)
    if args.command == "scan":
        return _scan(args)
    if args.command == "analysis-generation":
        return _analysis_generation(args)
    if args.command == "candidates":
        return _candidates_export(args)
    if args.command == "classification":
        return _classification_import(args)
    if args.command == "proposal":
        return {
            "create": _proposal_create,
            "list": _proposal_list,
            "show": _proposal_show,
            "decide": _proposal_decide,
            "mark-applied": _proposal_mark_applied,
        }[args.proposal_command](args)
    if args.command == "telemetry":
        return _telemetry_command(args)
    if args.command == "dashboard":
        return _dashboard_verify(args)
    if args.command == "db":
        return _db_command(args)
    if args.command == "schedule":
        return _schedule_command(args)
    raise AssertionError(args.command)


def _fail(code: int, exc: BaseException) -> NoReturn:
    _diagnostic(f"{type(exc).__name__}: {exc}")
    raise SystemExit(code)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = _dispatch(args)
    except ConfigurationError as exc:
        _fail(EXIT_CONFIG, exc)
    except CapabilityError as exc:
        _fail(EXIT_CAPABILITY, exc)
    except SourceError as exc:
        _fail(EXIT_SOURCE, exc)
    except (DatabaseError, sqlite3.Error) as exc:
        _fail(EXIT_DATABASE, exc)
    except (ValueError, KeyError, PermissionError) as exc:
        _fail(EXIT_VALIDATION, exc)
    except RuntimeError as exc:
        _fail(EXIT_CONFLICT, exc)
    except Exception as exc:
        _fail(EXIT_INTERNAL, exc)
    _emit(result)
    return 0

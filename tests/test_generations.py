from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agent_introspection import generations
from agent_introspection.database import connect_database
from agent_introspection.generations import (
    GenerationError,
    activate_generation,
    current_generation_id,
    stage_generation,
)


class RemoteEventStore:
    def __init__(self, *, present: bool = True) -> None:
        self.present = present

    def query(self, _sql: str, parameters: Mapping[str, str | int]) -> Iterable[Mapping[str, Any]]:
        if not self.present:
            return []
        return [
            {"event_id": value}
            for name, value in parameters.items()
            if name.removeprefix("event_").isdigit() and isinstance(value, str)
        ]


def _deliver_pending(connection: sqlite3.Connection, **_kwargs: object) -> dict[str, int]:
    rows = connection.execute(
        "SELECT event_id FROM otlp_outbox WHERE status = 'pending'"
    ).fetchall()
    with connection:
        connection.executemany(
            "UPDATE otlp_outbox SET status = 'delivered', delivered_at = 'now' WHERE event_id = ?",
            rows,
        )
    return {"selected": len(rows), "delivered": len(rows), "pending": 0}


def _seed_local_facts(connection: sqlite3.Connection, timestamp_ns: int) -> None:
    connection.execute(
        "INSERT INTO scan_runs (id, status, started_at) VALUES ('scan-1', 'succeeded', 'now')"
    )
    connection.execute(
        """
        INSERT INTO observations (
            id, scan_run_id, detector_id, detector_version, category, project_identity_id,
            task_identity, turn_identity, occurred_at_ns, fingerprint, operation_kind,
            target_kind, normalized_target, normalized_failure_class, normalization_version,
            membership_explanation, attributes_json, created_at
        ) VALUES (
            'observation-1', 'scan-1', 'tool_failure', 1, 'tool_failure', NULL,
            'task-1', NULL, ?, ?, 'tool', 'command', 'ruff', 'exit_code', 1,
            'deterministic membership', '{}', 'now'
        )
        """,
        (timestamp_ns, "a" * 64),
    )
    connection.execute(
        """
        INSERT INTO findings (
            id, fingerprint, category, project_identity_id, trend_state, detector_id,
            detector_version, first_seen_ns, last_seen_ns, occurrence_count,
            canonical_task_count, local_day_count, entity_version, updated_at
        ) VALUES (
            'finding-1', ?, 'tool_failure', NULL, 'actionable', 'tool_failure', 1,
            ?, ?, 1, 1, 1, 2, 'now'
        )
        """,
        ("b" * 64, timestamp_ns, timestamp_ns),
    )
    connection.commit()


def test_stage_and_remote_verified_activation_promote_one_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = connect_database(tmp_path / "introspection.sqlite3")
    try:
        now = datetime(2026, 7, 17, 12, tzinfo=UTC)
        timestamp_ns = int((now - timedelta(hours=1)).timestamp() * 1_000_000_000)
        _seed_local_facts(connection, timestamp_ns)
        staged = stage_generation(
            connection,
            source_contract_fingerprint="c" * 64,
            end_time=now,
        )
        assert current_generation_id(connection) is None
        assert len(staged.projection_event_ids) == 2
        payloads = [
            json.loads(row[0])
            for row in connection.execute("SELECT payload_json FROM otlp_outbox ORDER BY event_id")
        ]
        assert {payload["analysis.generation"] for payload in payloads} == {staged.generation_id}
        assert {payload["event.scope"] for payload in payloads} == {
            f"generation:{staged.generation_id}"
        }
        monkeypatch.setattr("agent_introspection.generations.drain_outbox", _deliver_pending)

        activated = activate_generation(
            connection,
            generation_id=staged.generation_id,
            client=RemoteEventStore(),
            endpoint="http://collector.test/v1/logs",
        )

        assert activated.generation_id == staged.generation_id
        assert current_generation_id(connection) == staged.generation_id
        assert connection.execute(
            "SELECT COUNT(*) FROM analysis_generation_activations"
        ).fetchone() == (1,)
        marker = json.loads(
            connection.execute(
                "SELECT payload_json FROM otlp_outbox WHERE event_id = ?",
                (activated.activation_event_id,),
            ).fetchone()[0]
        )
        assert marker == {
            "analysis.generation": staged.generation_id,
            "entity.id": staged.generation_id,
            "entity.version": 1,
            "event.id": activated.activation_event_id,
            "event.name": "introspection.analysis_generation.activated",
            "event.scope": "operational",
            "event.sequence": 1,
            "timestamp_ns": marker["timestamp_ns"],
        }
    finally:
        connection.close()


def test_activation_requires_remote_confirmation_before_cursor_advance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = connect_database(tmp_path / "introspection.sqlite3")
    try:
        now = datetime(2026, 7, 17, 12, tzinfo=UTC)
        _seed_local_facts(connection, int(now.timestamp() * 1_000_000_000))
        staged = stage_generation(
            connection,
            source_contract_fingerprint="c" * 64,
            end_time=now,
        )
        monkeypatch.setattr("agent_introspection.generations.drain_outbox", _deliver_pending)

        with pytest.raises(GenerationError, match="not remotely verifiable"):
            activate_generation(
                connection,
                generation_id=staged.generation_id,
                client=RemoteEventStore(present=False),
                endpoint="http://collector.test/v1/logs",
            )

        assert current_generation_id(connection) is None
        assert connection.execute(
            "SELECT COUNT(*) FROM analysis_generation_activations"
        ).fetchone() == (0,)
    finally:
        connection.close()


def test_activation_reuses_the_pending_marker_until_remote_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = connect_database(tmp_path / "introspection.sqlite3")
    try:
        now = datetime(2026, 7, 17, 12, tzinfo=UTC)
        _seed_local_facts(connection, int(now.timestamp() * 1_000_000_000))
        staged = stage_generation(
            connection,
            source_contract_fingerprint="c" * 64,
            end_time=now,
        )
        monkeypatch.setattr("agent_introspection.generations.drain_outbox", _deliver_pending)

        class DelayedMarkerStore(RemoteEventStore):
            def query(
                self, _sql: str, parameters: Mapping[str, str | int]
            ) -> Iterable[Mapping[str, Any]]:
                if parameters["event_name"] == "introspection.analysis_generation.activated":
                    return []
                return super().query(_sql, parameters)

        with pytest.raises(GenerationError, match="activation is not remotely verifiable"):
            activate_generation(
                connection,
                generation_id=staged.generation_id,
                client=DelayedMarkerStore(),
                endpoint="http://collector.test/v1/logs",
            )

        marker_id = connection.execute(
            """
            SELECT event_id FROM analysis_generation_event_links
            WHERE generation_id = ? AND role = 'activation'
            """,
            (staged.generation_id,),
        ).fetchone()[0]
        activated = activate_generation(
            connection,
            generation_id=staged.generation_id,
            client=RemoteEventStore(),
            endpoint="http://collector.test/v1/logs",
        )

        assert activated.activation_event_id == marker_id
        assert current_generation_id(connection) == staged.generation_id
    finally:
        connection.close()


def test_immutable_generation_provenance_rejects_pending_activation_link(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "introspection.sqlite3")
    try:
        now = datetime(2026, 7, 17, 12, tzinfo=UTC)
        _seed_local_facts(connection, int(now.timestamp() * 1_000_000_000))
        staged = stage_generation(
            connection,
            source_contract_fingerprint="c" * 64,
            end_time=now,
        )
        connection.execute(
            """
            INSERT INTO otlp_outbox (
                event_id, payload_json, status, attempt_count, next_attempt_at, created_at
            ) VALUES ('pending-activation', '{}', 'pending', 0, 'now', 'now')
            """
        )
        with pytest.raises(sqlite3.IntegrityError, match="projections must be delivered"):
            connection.execute(
                """
                INSERT INTO analysis_generation_event_links (generation_id, event_id, role)
                VALUES (?, 'pending-activation', 'activation')
                """,
                (staged.generation_id,),
            )
    finally:
        connection.close()


@pytest.mark.parametrize(
    "module_name",
    ("trends", "identities", "outcomes", "source", "scan_extraction"),
)
def test_material_semantic_contract_change_stages_a_new_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module_name: str
) -> None:
    connection = connect_database(tmp_path / "introspection.sqlite3")
    try:
        now = datetime(2026, 7, 17, 12, tzinfo=UTC)
        _seed_local_facts(connection, int(now.timestamp() * 1_000_000_000))
        initial = stage_generation(
            connection,
            source_contract_fingerprint="c" * 64,
            end_time=now,
        )
        monkeypatch.setattr("agent_introspection.generations.drain_outbox", _deliver_pending)
        activate_generation(
            connection,
            generation_id=initial.generation_id,
            client=RemoteEventStore(),
            endpoint="http://collector.test/v1/logs",
        )
        changed_contract = tmp_path / f"{module_name}.py"
        changed_contract.write_text(f"{module_name} contract changed\n")
        if module_name == "scan_extraction":
            monkeypatch.setattr(generations, "_SCAN_EXTRACTION_PATH", changed_contract)
        else:
            monkeypatch.setattr(
                getattr(generations, module_name), "__file__", str(changed_contract)
            )

        staged = stage_generation(
            connection,
            source_contract_fingerprint="c" * 64,
            end_time=now,
        )

        assert staged.generation_id != initial.generation_id
        assert staged.semantic_hash != initial.semantic_hash
        assert current_generation_id(connection) == initial.generation_id
    finally:
        connection.close()

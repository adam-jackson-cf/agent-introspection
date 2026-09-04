from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from agent_introspection.config import AppConfig, DatabaseConfig
from experiments.dashboard_prototype import cleanup
from experiments.dashboard_prototype.cleanup import CleanupError
from experiments.dashboard_prototype.recurrence_execution import canonical_hash


class _QueryClient:
    def __init__(self, event_ids: set[str]) -> None:
        self.event_ids = event_ids

    def query(self, sql: str, parameters: Mapping[str, str | int]) -> tuple[dict[str, Any], ...]:
        selected = {
            value.strip("'") for value in str(parameters["event_ids"])[1:-1].split(",") if value
        }
        if sql.startswith("ALTER TABLE"):
            self.event_ids -= selected
            return ()
        return tuple(
            {
                "event_id": event_id,
                "remote_namespace": cleanup.NAMESPACE,
                "immutable_tuple_count": 1,
                "row_count": 1,
            }
            for event_id in sorted(self.event_ids & selected)
        )


class _OmittedNamespaceQueryClient(_QueryClient):
    def query(self, sql: str, parameters: Mapping[str, str | int]) -> tuple[dict[str, Any], ...]:
        selected = {
            value.strip("'") for value in str(parameters["event_ids"])[1:-1].split(",") if value
        }
        if sql.startswith("ALTER TABLE"):
            return ()
        return tuple(
            {
                "event_id": event_id,
                "remote_namespace": "",
                "immutable_tuple_count": 1,
                "row_count": 1,
            }
            for event_id in sorted(self.event_ids & selected)
        )


def _manifest(path: Path, *, run_id: str, event_ids: list[str]) -> None:
    path.write_text(
        json.dumps(
            {
                "namespace": cleanup.NAMESPACE,
                "attempts": [
                    {
                        "run_id": run_id,
                        "cleanup_selector": {
                            "namespace": cleanup.NAMESPACE,
                            "run_id": run_id,
                            "event_ids": event_ids,
                        },
                    }
                ],
            }
        )
    )


def _outbox(path: Path, *, run_id: str, selected_id: str, unrelated_id: str) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE otlp_outbox (event_id TEXT PRIMARY KEY, payload_json TEXT, status TEXT)"
    )
    for event_id, scope in (
        (selected_id, cleanup.NAMESPACE),
        (unrelated_id, "unrelated-scope"),
    ):
        connection.execute(
            "INSERT INTO otlp_outbox VALUES (?, ?, 'delivered')",
            (
                event_id,
                json.dumps(
                    {
                        "event.id": event_id,
                        "event.name": cleanup._EVENT_FAMILIES["pipeline"],
                        "event.scope": scope,
                        "dashboard.run_id_hash": canonical_hash(run_id),
                    }
                ),
            ),
        )
    connection.commit()
    connection.close()


def test_cleanup_deletes_exact_remote_population_and_retains_local_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = "pipeline-exact-run"
    selected_id = "selected-event"
    unrelated_id = "unrelated-event"
    manifest = tmp_path / "pipeline-cleanup-manifest.json"
    database = tmp_path / "state.sqlite3"
    evidence = tmp_path / "cleanup-evidence.json"
    _manifest(manifest, run_id=run_id, event_ids=[selected_id])
    _outbox(
        database,
        run_id=run_id,
        selected_id=selected_id,
        unrelated_id=unrelated_id,
    )
    client = _QueryClient({selected_id, unrelated_id})
    monkeypatch.setattr(cleanup, "ClickHouseClient", lambda **_: client)

    preview = cleanup.execute_cleanup(
        config=AppConfig(database=DatabaseConfig(path=database)),
        manifest_paths=[manifest],
        evidence_path=evidence,
        apply=False,
    )
    assert preview["pre_cleanup"][0]["remote_present_event_count"] == 1
    stored_preview = json.loads(evidence.read_text())
    incident = {"approved_cleanup": False, "mutation_outcome": "unsafe deletion"}
    provenance = {"kind": "retained_harness_tool_result", "source_sha256": "abc123"}
    stored_preview["cleanup_incident"] = incident
    stored_preview["initial_reconciliation_provenance"] = provenance
    evidence.write_text(json.dumps(stored_preview))
    client.event_ids.remove(selected_id)

    result = cleanup.execute_cleanup(
        config=AppConfig(database=DatabaseConfig(path=database)),
        manifest_paths=[manifest],
        evidence_path=evidence,
        apply=True,
    )

    connection = sqlite3.connect(database)
    remaining = {
        row[0] for row in connection.execute("SELECT event_id FROM otlp_outbox").fetchall()
    }
    connection.close()
    assert remaining == {selected_id, unrelated_id}
    assert client.event_ids == {unrelated_id}
    assert result["post_cleanup"] == {
        "local_retained_event_count": 1,
        "local_retention_policy": "immutable otlp_outbox no-delete guard",
        "remote_event_count": 0,
    }
    assert result["pre_cleanup"][0]["remote_present_event_count"] == 0
    assert result["initial_reconciliation_summary"]["remote_present_event_count"] == 1
    assert result["cleanup_incident"] == incident
    assert result["initial_reconciliation_provenance"] == provenance
    assert result["resume_state"] == {
        "prior_reconciliation_reused": True,
        "remote_present_before_this_invocation": 0,
    }
    repeated = cleanup.execute_cleanup(
        config=AppConfig(database=DatabaseConfig(path=database)),
        manifest_paths=[manifest],
        evidence_path=evidence,
        apply=True,
    )
    assert repeated["initial_reconciliation_summary"] == result["initial_reconciliation_summary"]
    assert repeated["cleanup_incident"] == incident
    assert repeated["initial_reconciliation_provenance"] == provenance
    assert json.loads(evidence.read_text()) == repeated


def test_cleanup_rejects_prior_evidence_for_different_selector_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = "pipeline-exact-run"
    selected_id = "selected-event"
    manifest = tmp_path / "pipeline-cleanup-manifest.json"
    database = tmp_path / "state.sqlite3"
    evidence = tmp_path / "cleanup-evidence.json"
    _manifest(manifest, run_id=run_id, event_ids=[selected_id])
    _outbox(
        database,
        run_id=run_id,
        selected_id=selected_id,
        unrelated_id="unrelated-event",
    )
    monkeypatch.setattr(cleanup, "ClickHouseClient", lambda **_: _QueryClient({selected_id}))
    cleanup.execute_cleanup(
        config=AppConfig(database=DatabaseConfig(path=database)),
        manifest_paths=[manifest],
        evidence_path=evidence,
        apply=False,
    )
    prior = json.loads(evidence.read_text())
    prior["selector_hashes"] = ["different-selector"]
    evidence.write_text(json.dumps(prior))

    with pytest.raises(CleanupError, match="does not match exact selectors"):
        cleanup.execute_cleanup(
            config=AppConfig(database=DatabaseConfig(path=database)),
            manifest_paths=[manifest],
            evidence_path=evidence,
            apply=False,
        )


def test_cleanup_refuses_remote_row_without_exact_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = "pipeline-namespace-omitted"
    selected_id = "selected-event"
    manifest = tmp_path / "pipeline-cleanup-manifest.json"
    database = tmp_path / "state.sqlite3"
    evidence = tmp_path / "cleanup-evidence.json"
    _manifest(manifest, run_id=run_id, event_ids=[selected_id])
    _outbox(
        database,
        run_id=run_id,
        selected_id=selected_id,
        unrelated_id="unrelated-event",
    )
    client = _OmittedNamespaceQueryClient({selected_id})
    monkeypatch.setattr(cleanup, "ClickHouseClient", lambda **_: client)

    with pytest.raises(CleanupError, match="left 1 event identities"):
        cleanup.execute_cleanup(
            config=AppConfig(database=DatabaseConfig(path=database)),
            manifest_paths=[manifest],
            evidence_path=evidence,
            apply=True,
        )

    assert client.event_ids == {selected_id}


def test_cleanup_rejects_one_event_bound_to_multiple_runs(tmp_path: Path) -> None:
    first = tmp_path / "pipeline-cleanup-manifest-a.json"
    second = tmp_path / "pipeline-cleanup-manifest-b.json"
    _manifest(first, run_id="first-run", event_ids=["shared-event"])
    _manifest(second, run_id="second-run", event_ids=["shared-event"])

    with pytest.raises(CleanupError, match="multiple cleanup runs"):
        cleanup.load_cleanup_selectors([first, second])


def test_reconciliation_rejects_missing_local_exact_id(tmp_path: Path) -> None:
    manifest = tmp_path / "pipeline-cleanup-manifest.json"
    _manifest(manifest, run_id="missing-run", event_ids=["missing-event"])
    selector = cleanup.load_cleanup_selectors([manifest])[0]
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE otlp_outbox (event_id TEXT PRIMARY KEY, payload_json TEXT, status TEXT)"
    )

    with pytest.raises(CleanupError, match="missing exact IDs"):
        cleanup.reconcile_selector(connection, _QueryClient(set()), selector)

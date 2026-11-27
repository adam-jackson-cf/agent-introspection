import fcntl
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agent_introspection import hooks, spool
from agent_introspection.config import AppConfig
from tests.support.otlp_collector import Collector, hook_record, names, spool_records

pytestmark = pytest.mark.boundary


def test_a_dead_endpoint_and_a_timeout_keep_the_files_with_their_class(
    collector: Collector,
) -> None:
    url = collector.url
    collector.delay = 1.0

    slow = spool.send(url, b"{}", timeout=0.2)
    collector.close()
    refused = spool.send(url, b"{}", timeout=0.5)

    assert slow.action == "keep"
    assert slow.error_class == "TimeoutError"
    assert refused == spool.Outcome("keep", "ConnectionRefusedError")


# --- Draining ---------------------------------------------------------------------


def test_a_drain_sends_in_batches_of_fifty_and_removes_what_was_accepted(
    collector: Collector, inbox: Path
) -> None:
    spool_records(inbox, 120)

    result = spool.drain(collector.url)

    assert [len(b["resourceLogs"][0]["scopeLogs"][0]["logRecords"]) for b in collector.bodies] == [
        50,
        50,
        20,
    ]
    assert (result.sent, result.batches, result.rejected, result.error_class) == (120, 3, 0, "")
    assert names(inbox) == set()
    assert spool.read_state(inbox)["sent_total"] == {"hook-event": 120}


def test_records_written_during_a_drain_are_sent_by_the_final_empty_pass(
    collector: Collector, inbox: Path
) -> None:
    spool_records(inbox, 3)
    late = [hook_record(index) for index in range(100, 102)]
    writes: list[Path] = []

    def write_once() -> None:
        if not writes:
            writes.extend(hooks.write(item, inbox) for item in late)

    collector.on_request = write_once

    result = spool.drain(collector.url)

    assert (result.sent, result.batches) == (5, 2)
    assert names(inbox) == set()
    assert {item["event_id"] for item in late} <= {item["event_id"] for item in collector.records}


def test_a_hook_process_drains_at_most_the_requested_batches(
    collector: Collector, inbox: Path
) -> None:
    spool_records(inbox, 120)

    result = spool.drain(collector.url, max_batches=1)

    assert (len(collector.bodies), result.sent) == (1, 50)
    assert len(names(inbox)) == 70


def test_the_budget_ends_a_drain_between_batches(collector: Collector, inbox: Path) -> None:
    spool_records(inbox, 120)
    ticks = iter([0.0, 1.0, 100.0])

    result = spool.drain(collector.url, budget=20, clock=lambda: next(ticks))

    assert (len(collector.bodies), result.sent) == (1, 50)


def test_a_second_drain_while_one_runs_does_nothing(collector: Collector, inbox: Path) -> None:
    spool_records(inbox, 2)
    with (inbox / ".drain.lock").open("a") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        result = spool.drain(collector.url)

    assert result.busy
    assert collector.bodies == []
    assert len(names(inbox)) == 2


def test_a_partial_success_quarantines_the_batch_for_review(
    collector: Collector, inbox: Path
) -> None:
    spool_records(inbox, 2)
    collector.script = [(200, '{"partialSuccess":{"rejectedLogRecords":"1"}}')]

    result = spool.drain(collector.url)

    assert (result.sent, result.rejected) == (0, 2)
    assert names(inbox) == set()
    assert len(names(inbox / "rejected")) == 2
    assert spool.rejected_count(inbox) == 2
    assert spool.read_state(inbox)["last_error_class"] == "partial_success"


@pytest.mark.parametrize(
    ("status", "body", "error_class"),
    [
        (404, "", "endpoint_misconfigured"),
        (415, "", "endpoint_misconfigured"),
        (429, "", "http_429"),
        (500, "", "http_500"),
        (503, "{}", "http_503"),
    ],
)
def test_a_misconfigured_or_unavailable_endpoint_keeps_every_file(
    collector: Collector, inbox: Path, status: int, body: str, error_class: str
) -> None:
    spool_records(inbox, 3)
    collector.default = (status, body)

    result = spool.drain(collector.url)

    assert result.error_class == error_class
    assert len(collector.bodies) == 1
    assert len(names(inbox)) == 3
    assert not (inbox / "rejected").exists()
    assert spool.read_state(inbox)["last_error_class"] == error_class
    assert f" spool {error_class} 3" in hooks.log_file().read_text()


def test_a_record_the_spool_cannot_read_is_quarantined_and_the_rest_still_sent(
    collector: Collector, inbox: Path
) -> None:
    (item,) = spool_records(inbox, 1)
    (inbox / "broken.json").write_text("{not json")
    (inbox / "foreign.json").write_text('{"schema":"other/1","event_id":"x"}')

    result = spool.drain(collector.url)

    assert (result.sent, result.rejected) == (1, 2)
    assert [r["event_id"] for r in collector.records] == [item["event_id"]]
    assert names(inbox / "rejected") == {"broken.json", "foreign.json"}


def test_a_leftover_record_of_a_retired_family_is_deleted_not_quarantined(
    collector: Collector, inbox: Path
) -> None:
    spool_records(inbox, 1)
    leftover = inbox / "project.json"
    leftover.write_text('{"schema":"agent-introspection.session-project/1","event_id":"p"}')

    result = spool.drain(collector.url)

    assert (result.sent, result.rejected) == (1, 0)
    assert not leftover.exists()
    assert not (inbox / "rejected").exists()


def test_the_logged_class_never_includes_record_text_or_the_endpoint(
    collector: Collector, inbox: Path
) -> None:
    spool_records(inbox, 1)
    collector.default = (404, "secret response text")

    spool.drain(collector.url)

    log = hooks.log_file().read_text()
    assert "secret" not in log
    assert "127.0.0.1" not in log
    assert "key-0" not in log


# --- Status and sync --------------------------------------------------------------


def test_the_status_record_reports_backlog_age_counters_and_last_error(
    collector: Collector, inbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(spool, "host_name", lambda: "omarchy")
    monkeypatch.setattr(spool, "cli_version", lambda: "1.2.3")
    spool_records(inbox, 2)
    spool.drain(collector.url)
    (inbox / "rejected").mkdir()
    (inbox / "rejected" / "old.json").write_text("{}")
    spool_records(inbox, 3)
    now = datetime(2026, 10, 12, 12, 0, 0, tzinfo=UTC)
    stamp = now.timestamp() - 90
    for path in inbox.glob("*.json"):
        os.utime(path, (stamp, stamp))
    collector.default = (503, "")
    spool.drain(collector.url)

    status = spool.status_record(inbox, now=now)

    assert status["schema"] == spool.STATUS_SCHEMA
    assert (status["pending"], status["rejected"], status["oldest_pending_seconds"]) == (3, 1, 90)
    assert status["sent_total"] == {"hook-event": 2}
    assert status["last_error_class"] == "http_503"
    assert status["cli_version"] == "1.2.3"
    assert status["reported_at"] == "2026-10-12T12:00:00.000000Z"
    assert status["event_id"] == spool.status_record(inbox, now=now)["event_id"]
    empty = spool.status_record(inbox.parent / "none", now=now)
    assert (empty["pending"], empty["oldest_pending_seconds"]) == (0, None)


def sync_config(collector: Collector) -> AppConfig:
    return AppConfig(hooks_otlp_logs_endpoint=collector.url)


def test_sync_reports_status_after_draining(collector: Collector, inbox: Path) -> None:
    spool_records(inbox, 1)

    result = spool.sync(sync_config(collector))

    assert result["drain"]["sent"] == 2
    assert {r["schema"] for r in collector.records} == {hooks.SCHEMA, spool.STATUS_SCHEMA}
    assert names(inbox) == set()


def test_sync_ships_records_before_a_heartbeat_that_counts_them(
    collector: Collector, inbox: Path
) -> None:
    spool_records(inbox, 1)

    result = spool.sync(sync_config(collector))

    per_request = [
        [json.loads(e["body"]["stringValue"])["schema"] for e in body_logs(body)]
        for body in collector.bodies
    ]
    assert per_request == [[hooks.SCHEMA], [spool.STATUS_SCHEMA]]
    (heartbeat,) = [r for r in collector.records if r["schema"] == spool.STATUS_SCHEMA]
    assert heartbeat["sent_total"] == {"hook-event": 1}
    assert result["drain"]["sent"] == 2
    assert result["drain"]["batches"] == 2


def seed_stale_heartbeat(inbox: Path) -> str:
    stale = spool.status_record(inbox, now=datetime(2026, 10, 1, tzinfo=UTC))
    hooks.write(stale, inbox)
    return str(stale["event_id"])


def heartbeat_files(inbox: Path) -> list[dict[str, Any]]:
    items = [json.loads(path.read_text()) for path in inbox.glob("*.json")]
    return [item for item in items if item["schema"] == spool.STATUS_SCHEMA]


def test_sync_sends_the_heartbeat_alone_when_the_record_drain_stops_early(
    collector: Collector, inbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale_id = seed_stale_heartbeat(inbox)
    spool_records(inbox, 120)
    collector.script = [(200, "{}"), (503, "")]

    spool.sync(sync_config(collector))

    last = [e["body"]["stringValue"] for e in body_logs(collector.bodies[-1])]
    assert [json.loads(text)["schema"] for text in last] == [spool.STATUS_SCHEMA]
    assert stale_id not in {r["event_id"] for r in collector.records}
    (beat,) = [r for r in collector.records if r["schema"] == spool.STATUS_SCHEMA]
    assert beat["sent_total"] == {"hook-event": 50}
    assert beat["pending"] == 70
    assert heartbeat_files(inbox) == []


def test_sync_spools_only_the_fresh_heartbeat_when_it_cannot_be_sent(
    collector: Collector, inbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale_id = seed_stale_heartbeat(inbox)
    collector.default = (503, "")

    spool.sync(sync_config(collector))

    (kept,) = heartbeat_files(inbox)
    assert kept["event_id"] != stale_id


def test_sync_skips_the_heartbeat_while_another_drain_holds_the_lock(
    collector: Collector, inbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spool_records(inbox, 2)
    with (inbox / ".drain.lock").open("a") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        result = spool.sync(sync_config(collector))

    assert result["drain"]["busy"] is True
    assert collector.bodies == []
    assert heartbeat_files(inbox) == []


def body_logs(body: dict[str, Any]) -> list[dict[str, Any]]:
    logs: list[dict[str, Any]] = body["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
    return logs


def test_sync_keeps_the_spool_when_the_collector_is_unreachable(inbox: Path) -> None:
    spool_records(inbox, 1)

    result = spool.sync(AppConfig(hooks_otlp_logs_endpoint="http://127.0.0.1:9/v1/logs"))

    assert result["drain"]["sent"] == 0
    assert hooks.SCHEMA in {json.loads(path.read_text())["schema"] for path in inbox.glob("*.json")}

import fcntl
import io
import json
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from agent_introspection import hooks, sessions, spool
from agent_introspection.config import AppConfig
from tests.support.otlp_collector import Collector, hook_record, names, spool_records
from tests.test_sessions import store, write_jsonl

# --- OTLP body --------------------------------------------------------------------


def test_the_body_is_one_resource_with_a_compact_record_per_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(spool, "host_name", lambda: "omarchy")
    monkeypatch.setattr(spool, "cli_version", lambda: "9.9.9")
    item = hook_record(1)

    document = json.loads(spool.build_body([item]))

    (resource_logs,) = document["resourceLogs"]
    resource = {
        a["key"]: a["value"]["stringValue"] for a in resource_logs["resource"]["attributes"]
    }
    assert resource == {
        "service.name": "agent-introspection",
        "host.name": "omarchy",
        "agent_introspection.cli_version": "9.9.9",
    }
    (scope,) = resource_logs["scopeLogs"]
    assert scope["scope"]["name"] == "agent-introspection.spool"
    (entry,) = scope["logRecords"]
    nanos = int(datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC).timestamp()) * 10**9 + 123_456_000
    assert entry["timeUnixNano"] == str(nanos)
    assert "observedTimeUnixNano" not in entry
    assert entry["severityText"] == "INFO"
    assert entry["body"]["stringValue"] == json.dumps(item, separators=(",", ":"))
    attributes = {a["key"]: a["value"]["stringValue"] for a in entry["attributes"]}
    assert attributes == {
        "event.name": "agent_introspection.hook_event",
        "agent_introspection.schema": hooks.SCHEMA,
        "agent_introspection.event_id": item["event_id"],
    }


@pytest.mark.parametrize(
    ("schema", "time_field", "event_name"),
    [
        (spool.SESSION_PROJECT_SCHEMA, "resolved_at", "agent_introspection.session_project"),
        (spool.STATUS_SCHEMA, "reported_at", "agent_introspection.spool_status"),
    ],
)
def test_every_family_is_named_and_timed_by_its_own_field(
    schema: str, time_field: str, event_name: str
) -> None:
    item = {"schema": schema, "event_id": "e", time_field: "2026-10-01T00:00:00Z"}

    (entry,) = json.loads(spool.build_body([item]))["resourceLogs"][0]["scopeLogs"][0]["logRecords"]

    attributes = {a["key"]: a["value"]["stringValue"] for a in entry["attributes"]}
    assert attributes["event.name"] == event_name
    assert entry["timeUnixNano"] == str(int(datetime(2026, 10, 1, tzinfo=UTC).timestamp()) * 10**9)


# --- Responses --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "body", "action", "error_class"),
    [
        (200, "", "delete", ""),
        (200, "{}", "delete", ""),
        (200, '{"partialSuccess":{}}', "delete", ""),
        (200, '{"partialSuccess":{"rejectedLogRecords":"0"}}', "delete", ""),
        (200, '{"partialSuccess":{"rejectedLogRecords":0,"errorMessage":"warn"}}', "delete", ""),
        (200, '{"partialSuccess":{"rejectedLogRecords":"3"}}', "reject", "partial_success"),
        (200, '{"partialSuccess":{"rejectedLogRecords":3}}', "reject", "partial_success"),
        (200, "<html>ok</html>", "keep", "unparseable_response"),
        (200, '{"partialSuccess":{"rejectedLogRecords":"x"}}', "keep", "unparseable_response"),
        (400, '{"code":3,"message":"bad"}', "reject", "rejected_batch"),
        (400, "<html>bad request</html>", "keep", "endpoint_misconfigured"),
        (404, "", "keep", "endpoint_misconfigured"),
        (405, "", "keep", "endpoint_misconfigured"),
        (415, '{"code":3,"message":"content type"}', "keep", "endpoint_misconfigured"),
        (429, "", "keep", "http_429"),
        (503, "", "keep", "http_503"),
    ],
)
def test_responses_are_classified_by_what_they_say_about_the_batch(
    status: int, body: str, action: str, error_class: str
) -> None:
    outcome = spool.classify_response(status, body.encode())
    assert (outcome.action, outcome.error_class) == (action, error_class)


def test_an_http_error_response_is_read_then_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    body = io.BytesIO(b"unavailable")
    error = urllib.error.HTTPError("http://collector/v1/logs", 503, "Busy", Message(), body)

    def failing(request: object, timeout: float) -> None:
        raise error

    monkeypatch.setattr(urllib.request, "urlopen", failing)

    assert spool.send("http://collector/v1/logs", b"{}") == spool.Outcome("keep", "http_503")
    assert body.closed


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
    assert f" spool {error_class} 3" in hooks.LOG.read_text()


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


def test_the_logged_class_never_includes_record_text_or_the_endpoint(
    collector: Collector, inbox: Path
) -> None:
    spool_records(inbox, 1)
    collector.default = (404, "secret response text")

    spool.drain(collector.url)

    log = hooks.LOG.read_text()
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
    assert status["sent_total"] == {"hook-event": 2, "session-project": 0}
    assert status["last_error_class"] == "http_503"
    assert status["cli_version"] == "1.2.3"
    assert status["reported_at"] == "2026-10-12T12:00:00.000000Z"
    assert status["event_id"] == spool.status_record(inbox, now=now)["event_id"]
    empty = spool.status_record(inbox.parent / "none", now=now)
    assert (empty["pending"], empty["oldest_pending_seconds"]) == (0, None)


def sync_config(collector: Collector | None) -> AppConfig:
    return AppConfig(hooks_otlp_logs_endpoint=collector.url if collector else None)


def one_session_store(tmp_path: Path) -> sessions.Store:
    root = tmp_path / "projects"
    write_jsonl(
        root / "a" / "one.jsonl",
        {"sessionId": "s1", "cwd": "/nowhere", "timestamp": "2026-10-01T10:00:00Z"},
    )
    result: sessions.Store = store("claude-code", root)
    return result


def test_sync_attributes_then_reports_status_then_drains(
    collector: Collector, inbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sessions, "load_stores", lambda _roots=None: [one_session_store(tmp_path)])

    result = spool.sync(sync_config(collector), scan_state=tmp_path / "scan.json", environ={})

    assert result["sessions"] == {"sessions": 1, "missing_workspace": 1}
    assert result["drain"]["sent"] == 2
    assert {r["schema"] for r in collector.records} == {
        spool.SESSION_PROJECT_SCHEMA,
        spool.STATUS_SCHEMA,
    }
    assert names(inbox) == set()


def test_sync_ships_records_before_a_heartbeat_that_counts_them(
    collector: Collector, inbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sessions, "load_stores", lambda _roots=None: [one_session_store(tmp_path)])

    result = spool.sync(sync_config(collector), scan_state=tmp_path / "scan.json", environ={})

    per_request = [
        [json.loads(e["body"]["stringValue"])["schema"] for e in body_logs(body)]
        for body in collector.bodies
    ]
    assert per_request == [[spool.SESSION_PROJECT_SCHEMA], [spool.STATUS_SCHEMA]]
    (heartbeat,) = [r for r in collector.records if r["schema"] == spool.STATUS_SCHEMA]
    assert heartbeat["sent_total"] == {"hook-event": 0, "session-project": 1}
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
    monkeypatch.setattr(sessions, "load_stores", lambda _roots=None: [])
    stale_id = seed_stale_heartbeat(inbox)
    spool_records(inbox, 120)
    collector.script = [(200, "{}"), (503, "")]

    spool.sync(sync_config(collector), scan_state=tmp_path / "scan.json", environ={})

    last = [e["body"]["stringValue"] for e in body_logs(collector.bodies[-1])]
    assert [json.loads(text)["schema"] for text in last] == [spool.STATUS_SCHEMA]
    assert stale_id not in {r["event_id"] for r in collector.records}
    (beat,) = [r for r in collector.records if r["schema"] == spool.STATUS_SCHEMA]
    assert beat["sent_total"] == {"hook-event": 50, "session-project": 0}
    assert beat["pending"] == 70
    assert heartbeat_files(inbox) == []


def test_sync_spools_only_the_fresh_heartbeat_when_it_cannot_be_sent(
    collector: Collector, inbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sessions, "load_stores", lambda _roots=None: [])
    stale_id = seed_stale_heartbeat(inbox)
    collector.default = (503, "")

    spool.sync(sync_config(collector), scan_state=tmp_path / "scan.json", environ={})

    (kept,) = heartbeat_files(inbox)
    assert kept["event_id"] != stale_id


def test_sync_skips_the_heartbeat_while_another_drain_holds_the_lock(
    collector: Collector, inbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sessions, "load_stores", lambda _roots=None: [])
    spool_records(inbox, 2)
    with (inbox / ".drain.lock").open("a") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        result = spool.sync(sync_config(collector), scan_state=tmp_path / "scan.json", environ={})

    assert result["drain"]["busy"] is True
    assert collector.bodies == []
    assert heartbeat_files(inbox) == []


def body_logs(body: dict[str, Any]) -> list[dict[str, Any]]:
    logs: list[dict[str, Any]] = body["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
    return logs


def test_sync_without_an_endpoint_only_spools_the_attribution(
    inbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sessions, "load_stores", lambda _roots=None: [one_session_store(tmp_path)])

    result = spool.sync(sync_config(None), scan_state=tmp_path / "scan.json", environ={})

    assert result["drain"] == {"skipped": "no_endpoint"}
    (written,) = inbox.glob("*.json")
    assert json.loads(written.read_text())["schema"] == spool.SESSION_PROJECT_SCHEMA

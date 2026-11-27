import io
import json
import urllib.error
from datetime import UTC, datetime
from email.message import Message

import pytest

from agent_introspection import hooks, spool
from tests.support.otlp_collector import hook_record

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


def test_the_status_family_is_named_and_timed_by_its_own_field() -> None:
    item = {"schema": spool.STATUS_SCHEMA, "event_id": "e", "reported_at": "2026-10-01T00:00:00Z"}

    (entry,) = json.loads(spool.build_body([item]))["resourceLogs"][0]["scopeLogs"][0]["logRecords"]

    attributes = {a["key"]: a["value"]["stringValue"] for a in entry["attributes"]}
    assert attributes["event.name"] == "agent_introspection.spool_status"
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

    monkeypatch.setattr(spool, "_urlopen", failing)

    assert spool.send("http://collector/v1/logs", b"{}") == spool.Outcome("keep", "http_503")
    assert body.closed

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_introspection import hooks, spool
from tests.support.otlp_collector import Collector, names, spool_records

pytestmark = pytest.mark.boundary

SESSION = "sess-1"


def hook_config(tmp_path: Path, endpoint: str | None) -> Path:
    config = tmp_path / "config.toml"
    config.write_text("" if endpoint is None else f'[hooks]\notlp_logs_endpoint = "{endpoint}"\n')
    return config


def feed_envelope(monkeypatch: pytest.MonkeyPatch, **fields: object) -> None:
    envelope = {"session_id": SESSION, "tool_name": "Bash", "tool_input": {"command": "ls"}}
    monkeypatch.setattr(hooks, "read_stdin", lambda: json.dumps(envelope | fields))


def test_a_hook_drains_one_batch_of_the_spool_and_leaves_the_rest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, inbox: Path, collector: Collector
) -> None:
    spool_records(inbox, 60)
    feed_envelope(monkeypatch)

    assert hooks.main("claude-code", "PreToolUse", hook_config(tmp_path, collector.url)) == 0

    assert len(collector.bodies) == 1
    assert len(collector.records) == spool.BATCH_SIZE
    assert len(names(inbox)) == 61 - spool.BATCH_SIZE


def test_a_hook_without_hooks_config_sends_to_the_collectors_logs_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, inbox: Path, collector: Collector
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(f'[signoz]\notlp_endpoint = "{collector.base}"\n')
    feed_envelope(monkeypatch)

    assert hooks.main("claude-code", "PreToolUse", config) == 0

    assert len(collector.records) == 1
    assert names(inbox) == set()


def test_the_otel_environment_never_redirects_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, inbox: Path, collector: Collector
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", collector.base)
    feed_envelope(monkeypatch)

    assert (
        hooks.main("claude-code", "PreToolUse", hook_config(tmp_path, "http://127.0.0.1:9/v1/logs"))
        == 0
    )

    assert collector.bodies == []
    assert len(names(inbox)) == 1


def test_a_hook_survives_an_unreachable_collector_and_an_invalid_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, inbox: Path
) -> None:
    config = hook_config(tmp_path, "http://127.0.0.1:9/v1/logs")
    feed_envelope(monkeypatch)
    assert hooks.main("claude-code", "PreToolUse", config) == 0
    assert len(names(inbox)) == 1
    assert " spool ConnectionRefusedError 1" in hooks.log_file().read_text()

    config.write_text("[hooks\n")
    feed_envelope(monkeypatch, tool_name="Read")
    assert hooks.main("claude-code", "PreToolUse", config) == 0
    assert " spool ConfigurationError 0" in hooks.log_file().read_text()
    assert len(names(inbox)) == 2

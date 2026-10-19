from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest

from agent_introspection import capabilities, facts, producers, profiles

_DAY_NS = 86_400 * 1_000_000_000
_FIRST_NS = 1_791_000_000 * 1_000_000_000


@dataclass
class FakeService:
    """One service's telemetry, by name: what the fake ClickHouse aggregates."""

    spans: dict[str, int] = field(default_factory=dict)
    events: dict[str, int] = field(default_factory=dict)
    span_keys: dict[tuple[str, str], int] = field(default_factory=dict)
    log_keys: dict[tuple[str, str], int] = field(default_factory=dict)
    span_rows: int = 0
    log_rows: int = 0


def _line(row: dict[str, object]) -> str:
    # ClickHouse quotes 64-bit integers in JSONEachRow.
    return json.dumps({
        key: str(value) if isinstance(value, int) else value for key, value in row.items()
    })


def _top(counts: dict[Any, int], limit: int) -> list[tuple[Any, int]]:
    return sorted(counts.items(), key=lambda item: (-item[1], str(item[0])))[:limit]


class FakeClickHouse:
    """Answers the producers queries by shape, and records every query it was sent."""

    def __init__(self, services: dict[str, FakeService]) -> None:
        self.services = services
        self.queries: list[str] = []

    def __call__(self, sql: str) -> str:
        self.queries.append(sql)
        match = re.search(r"(?:serviceName|::String) = '([^']*)'", sql)
        service = match[1] if match else None
        limit = int(m[1]) if (m := re.search(r"LIMIT (\d+)", sql)) else 0
        if "sum(span_rows)" in sql:
            return self._activity(service)
        found = self.services.get(service or "", FakeService())
        if "ARRAY JOIN" in sql:
            keys = found.span_keys if producers.SPAN_TABLE in sql else found.log_keys
            return self._keys(keys, limit)
        names = found.events if "event.name" in sql else found.spans
        rows = _top(names, limit)
        return "\n".join(
            _line({"name": name, "rows": count, "distinct_names": len(names)})
            for name, count in rows
        )

    def _activity(self, service: str | None) -> str:
        chosen = {
            name: item for name, item in self.services.items() if service is None or name == service
        }
        return "\n".join(
            _line({
                "service": name,
                "span_rows": item.span_rows,
                "log_rows": item.log_rows,
                "first_ns": _FIRST_NS,
                "last_ns": _FIRST_NS + _DAY_NS,
            })
            for name, item in chosen.items()
            if item.span_rows or item.log_rows
        )

    @staticmethod
    def _keys(keys: dict[tuple[str, str], int], limit: int) -> str:
        lines = []
        for kind in producers.KEY_KINDS:
            family = {key: rows for (each, key), rows in keys.items() if each == kind}
            lines += [
                _line({"family": kind, "key": key, "rows": rows, "distinct_names": len(family)})
                for key, rows in _top(family, limit)
            ]
        return "\n".join(lines)


def tool_producer() -> FakeService:
    """A producer with tool spans, a session key, model usage logs, and content keys."""
    return FakeService(
        spans={"Tool.execute": 40, "LLM.run": 10, "/Users/alice/build": 2, "/home/bob/build": 3},
        events={"api_request": 12, "user_prompt": 4},
        span_keys={
            ("string", "tool.name"): 40,
            ("string", "session.id"): 40,
            ("string", "tool.arguments"): 40,
            ("string", "message.id"): 40,
            ("number", "duration_ms"): 40,
            ("resource", "service.name"): 55,
        },
        log_keys={
            ("string", "event.name"): 16,
            ("string", "prompt"): 4,
            ("number", "input_tokens"): 12,
            ("bool", "success"): 12,
            ("resource", "service.name"): 16,
        },
        span_rows=55,
        log_rows=16,
    )


def fake_services() -> dict[str, FakeService]:
    return {
        "claude-code": FakeService(spans={"claude_code.interaction": 5}, span_rows=5, log_rows=50),
        "codex_exec": FakeService(spans={"session_task.turn": 900}, span_rows=900),
        "opencode": FakeService(spans={"Tool.execute": 5000}, span_rows=5000),
        "agent-introspection": FakeService(log_rows=30),
        "toolbox": tool_producer(),
        "telemetrygen": FakeService(span_rows=6),
    }


def test_the_registry_lists_known_producers_that_are_not_harnesses() -> None:
    registry = facts.load_registry()

    assert registry.producers == {"opencode": "opencode"}
    assert not set(registry.producers) & set(registry.harnesses)


def test_discover_classes_each_service_and_orders_by_class_then_rows() -> None:
    run = FakeClickHouse(fake_services())

    result = producers.discover(run, facts.load_registry(), ["claude-code"], 7)

    services = {entry["service"]: entry for entry in result["services"]}
    assert [entry["service"] for entry in result["services"]] == [
        "codex_exec",
        "claude-code",
        "opencode",
        "agent-introspection",
        "toolbox",
        "telemetrygen",
    ]
    assert [services[name]["class"] for name in ("codex_exec", "opencode")] == [
        "registered",
        "known_producer",
    ]
    assert services["agent-introspection"]["class"] == "internal"
    assert services["toolbox"]["class"] == "unregistered"
    assert (services["claude-code"]["enabled"], services["codex_exec"]["enabled"]) == (True, False)
    assert services["opencode"]["enabled"] is None
    assert services["codex_exec"]["profile"] == "codex"
    assert services["opencode"]["profile"] == "opencode"
    assert services["toolbox"]["profile"] is None
    assert services["toolbox"]["first_seen"] == "2026-10-03T04:00:00Z"
    assert (services["toolbox"]["span_rows"], services["toolbox"]["log_rows"]) == (55, 16)
    assert result["enabled_not_seen"] == []


def test_discover_names_enabled_harnesses_with_no_rows() -> None:
    run = FakeClickHouse({"claude-code": FakeService(span_rows=1)})

    result = producers.discover(run, facts.load_registry(), ["claude-code", "oh-my-pi"], 3)

    assert result["enabled_not_seen"] == ["oh-my-pi"]
    assert "INTERVAL 3 DAY" in run.queries[0]


def test_a_window_below_one_day_is_refused() -> None:
    with pytest.raises(producers.ProducerError, match="at least 1"):
        producers.discover(FakeClickHouse({}), facts.load_registry(), None, 0)


def test_rows_without_a_service_name_are_one_unregistered_entry_no_command_targets(
    tmp_path: Path,
) -> None:
    run = FakeClickHouse({
        "": FakeService(span_rows=3),
        "  ": FakeService(log_rows=2),
        "toolbox": tool_producer(),
    })

    found = producers.discover(run, facts.load_registry(), None, 7)["services"]
    report = producers.drift_report(run, tmp_path, [])

    assert [entry["service"] for entry in found] == ["toolbox", producers.EMPTY_SERVICE]
    assert found[1] == {
        "service": "(empty)",
        "span_rows": 3,
        "log_rows": 2,
        "first_seen": found[0]["first_seen"],
        "last_seen": found[0]["last_seen"],
        "class": "unregistered",
        "enabled": None,
        "profile": None,
        "note": producers.EMPTY_SERVICE_NOTE,
    }
    # drift_report's unregistered names are what `doctor --drift` tells the user to
    # profile, so each must be a name `producers profile` accepts.
    assert report["unregistered"] == ["toolbox"]
    assert report["unnamed_rows"] == 5
    with pytest.raises(producers.ProducerError, match=r"non-empty service\.name"):
        producers.profile(run, "  ", 7, facts.load_registry())


def test_profile_reports_names_and_keys_with_counts_and_redacts_home_paths() -> None:
    run = FakeClickHouse(fake_services())

    result = producers.profile(run, "toolbox", 7, facts.load_registry())

    assert (result["class"], result["profile"]) == ("unregistered", None)
    assert (result["span_rows"], result["log_rows"]) == (55, 16)
    assert result["span_names"]["names"] == [
        {"name": "Tool.execute", "rows": 40},
        {"name": "LLM.run", "rows": 10},
        {"name": "~/build", "rows": 5},
    ]
    assert result["event_names"]["names"][0] == {"name": "api_request", "rows": 12}
    assert result["resource_keys"]["spans"]["names"] == [{"name": "service.name", "rows": 55}]
    assert {entry["name"] for entry in result["attribute_keys"]["logs.number"]["names"]} == {
        "input_tokens"
    }
    assert set(result["attribute_keys"]) == {
        f"{source}.{kind}" for source in producers.SOURCES for kind in ("string", "number", "bool")
    }
    assert "/Users/" not in json.dumps(result)
    assert "/home/" not in json.dumps(result)


def test_profile_of_a_service_with_no_rows_reports_empty_lists() -> None:
    result = producers.profile(FakeClickHouse({}), "silent", 7, facts.load_registry())

    assert (result["span_rows"], result["first_seen"]) == (0, None)
    assert result["span_names"] == {"names": [], "distinct": 0, "dropped": 0}
    assert {guess["guess"] for guess in result["capabilities"]["guesses"]} == {"unverified"}


def test_profile_keeps_the_most_frequent_names_and_reports_what_it_dropped() -> None:
    many = FakeService(
        spans={f"span.{index:03d}": 1000 - index for index in range(205)},
        span_keys={("string", f"key.{index:03d}"): 10 for index in range(203)},
        span_rows=1,
    )
    result = producers.profile(FakeClickHouse({"many": many}), "many", 7, facts.load_registry())

    spans = result["span_names"]
    assert (len(spans["names"]), spans["distinct"], spans["dropped"]) == (200, 205, 5)
    assert spans["names"][0] == {"name": "span.000", "rows": 1000}
    assert producers.truncation_notes(result) == [
        "span names: kept the top 200 of 205 by rows; 5 dropped",
        "spans.string keys: kept the top 200 of 203 by rows; 3 dropped",
    ]


@pytest.mark.parametrize(
    ("key", "flagged"),
    [
        ("gen_ai.prompt", True),
        ("user_prompt", True),
        ("db.query.text", True),
        ("tool.arguments", True),
        ("args", True),
        ("tool_input", True),
        ("toolInput", True),
        ("output", True),
        ("gen_ai.input.messages", True),
        ("gen_ai.prompt.0.content", True),
        ("llm.input_messages.0", True),
        ("body", True),
        ("command", True),
        ("cmd", True),
        ("diff", True),
        ("patch", True),
        ("message.id", False),
        ("prompt.id", False),
        ("prompt_length", False),
        ("input_tokens", False),
        ("gen_ai.usage.output_tokens", False),
        ("content_type", False),
        ("tool.call_id", False),
        ("arguments_hash", False),
        ("command_shape", False),
        ("session.id", False),
        ("0", False),
    ],
)
def test_content_keys_are_judged_by_their_last_word(key: str, flagged: bool) -> None:
    assert producers.is_content_key(key) is flagged


def test_profile_flags_string_keys_that_may_hold_user_text_without_reading_them() -> None:
    result = producers.profile(FakeClickHouse(fake_services()), "toolbox", 7, facts.load_registry())

    content = result["content_keys"]
    assert content["keys"] == [
        {"family": "spans.string", "key": "tool.arguments", "rows": 40},
        {"family": "logs.string", "key": "prompt", "rows": 4},
    ]
    assert "not read" in content["note"]


def test_profile_guesses_capabilities_from_hints_and_labels_them_guesses() -> None:
    result = producers.profile(FakeClickHouse(fake_services()), "toolbox", 7, facts.load_registry())

    guesses = {guess["id"]: guess for guess in result["capabilities"]["guesses"]}
    assert [guess["id"] for guess in result["capabilities"]["guesses"]] == list(
        capabilities.CAPABILITY_IDS
    )
    assert guesses["tool_calls"]["guess"] == "native"
    assert {"kind": "attribute_keys", "name": "tool.name", "rows": 40} in guesses["tool_calls"][
        "evidence"
    ]
    assert guesses["export"]["guess"] == "native"
    assert guesses["session_identity"]["evidence"][0]["name"] == "session.id"
    assert "guess" in result["capabilities"]["note"]
    draft = result["draft"]
    assert draft["capabilities"]["tool_calls"]["status"] == "native"
    assert "not verified" in draft["capabilities"]["tool_calls"]["evidence"]


def test_guess_evidence_is_capped_most_rows_first() -> None:
    noisy = FakeService(
        span_keys={("number", f"tokens.{index:02d}"): index for index in range(1, 15)},
        span_rows=1,
    )
    telemetry = producers.collect(FakeClickHouse({"noisy": noisy}), "noisy", 7, 200)

    guesses = {guess["id"]: guess for guess in producers.guess_capabilities(telemetry)}

    usage = guesses["model_usage"]
    assert len(usage["evidence"]) == producers.EVIDENCE_LIMIT
    assert usage["evidence"][0] == {"kind": "attribute_keys", "name": "tokens.14", "rows": 14}
    assert usage["more_evidence"] == 4


def test_profile_projects_the_draft_and_previews_its_impact() -> None:
    registry = facts.load_registry()
    result = producers.profile(
        FakeClickHouse(fake_services()), "toolbox", 7, registry, enabled=["claude-code"]
    )

    assert set(result["projection"]) == set(registry.requires)
    assert set(result["projection"].values()) <= set(capabilities.OUTCOMES)
    impact = result["impact"]
    assert (impact["candidate"], impact["enabled"]) == ("toolbox", ["claude-code"])
    hidden = {entry["signal"] for entry in impact["hidden"]}
    assert set(impact["visible_after"]) == set(impact["visible_now"]) - hidden
    assert hidden, "a draft with unverified capabilities hides the signals that need them"


def test_the_draft_needs_completing_before_it_validates() -> None:
    result = producers.profile(FakeClickHouse(fake_services()), "toolbox", 7, facts.load_registry())

    draft = result["draft"]
    assert (draft["key"], draft["status"], draft["service_names"]) == (
        "toolbox",
        "candidate",
        ["toolbox"],
    )
    assert result["draft_check"]["valid"] is False
    assert "binary must be text" in result["draft_check"]["error"]
    assert set(result["draft_check"]["to_complete"]) == set(producers.DRAFT_TO_COMPLETE)


def _completed(text: str) -> str:
    return text.replace('binary = ""', 'binary = "toolbox"').replace(
        "verified_versions = []", 'verified_versions = ["1.0.0"]'
    )


def test_a_written_draft_round_trips_through_toml_and_loads_once_completed(
    tmp_path: Path,
) -> None:
    registry = facts.load_registry()
    result = producers.profile(FakeClickHouse(fake_services()), "toolbox", 7, registry)
    path = tmp_path / "toolbox.toml"

    assert producers.write_draft(result["draft"], path) == path.resolve()

    text = path.read_text()
    assert tomllib.loads(text) == result["draft"]
    assert "# complete:" in text
    path.write_text(_completed(text))
    loaded = profiles.load_profile(path, registry)
    assert (loaded.key, loaded.status, loaded.service_names) == (
        "toolbox",
        "candidate",
        ("toolbox",),
    )
    assert {name: claim.status for name, claim in loaded.capabilities.items()} == {
        name: claim["status"] for name, claim in result["draft"]["capabilities"].items()
    }
    assert producers.check_draft(tomllib.loads(path.read_text()), registry)["valid"] is True


def test_draft_keys_are_derived_from_the_service_name() -> None:
    assert producers.draft_key("Codex Exec_Server") == "codex-exec-server"
    with pytest.raises(producers.ProducerError, match="cannot derive"):
        producers.draft_key("___")


@pytest.mark.parametrize(
    "text",
    ['quote " and backslash \\', "line\nbreak\ttab", "del \x7f and nul \x00", "café 😀", ""],
)
def test_toml_strings_round_trip(text: str) -> None:
    document = {"value": text, "table": {"inline": {"text": text, "list": [text], "flag": True}}}

    assert tomllib.loads(producers.render_draft(document)) == document


def test_toml_writer_quotes_keys_and_rejects_types_outside_the_schema() -> None:
    document = {"a.b": 1, "table": {"dotted.key": False, "empty": {}}}

    assert tomllib.loads(producers.render_draft(document)) == document
    with pytest.raises(producers.ProducerError, match="float"):
        producers.toml_value(1.5)


def test_write_draft_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    path = tmp_path / "draft.toml"
    path.write_text("kept")
    draft = {"schema": profiles.SCHEMA}

    with pytest.raises(producers.ProducerError, match="exists; pass --force"):
        producers.write_draft(draft, path)
    assert path.read_text() == "kept"

    producers.write_draft(draft, path, force=True)
    assert tomllib.loads(path.read_text()) == draft


def test_write_draft_refuses_the_bundled_profiles_and_bad_paths(tmp_path: Path) -> None:
    bundled = Path(str(files(profiles.PACKAGE)))
    before = sorted(item.name for item in bundled.iterdir())

    with pytest.raises(producers.ProducerError, match="bundled profiles"):
        producers.write_draft({}, bundled / "draft.toml", force=True)
    with pytest.raises(producers.ProducerError, match="bundled profiles"):
        producers.draft_target(bundled)
    with pytest.raises(producers.ProducerError, match="is a directory"):
        producers.draft_target(tmp_path, force=True)
    with pytest.raises(producers.ProducerError, match="is not a directory"):
        producers.draft_target(tmp_path / "missing" / "draft.toml")
    assert sorted(item.name for item in bundled.iterdir()) == before


def test_write_draft_follows_a_symlink_before_judging_the_target(tmp_path: Path) -> None:
    link = tmp_path / "profiles"
    link.symlink_to(Path(str(files(profiles.PACKAGE))), target_is_directory=True)

    with pytest.raises(producers.ProducerError, match="bundled profiles"):
        producers.draft_target(link / "draft.toml")


def _attribute_maps(sql: str) -> list[str]:
    return re.findall(
        r"(?:attributes_string|attributes_number|attributes_bool|resources_string)\[[^\]]*\]", sql
    )


def test_every_query_reads_names_only_and_is_time_bounded(tmp_path: Path) -> None:
    run = FakeClickHouse(fake_services())
    registry = facts.load_registry()
    producers.discover(run, registry, None, 7)
    producers.profile(run, "toolbox", 7, registry)
    producers.save_fingerprint(producers.fingerprint(run, ["toolbox"], 7)["toolbox"], tmp_path)
    producers.drift_report(run, tmp_path, ["toolbox", "claude-code"])

    assert len(run.queries) >= 15
    for sql in run.queries:
        assert f"SETTINGS max_execution_time = {producers.QUERY_SECONDS}" in sql
        assert "mapValues" not in sql
        assert "SELECT *" not in sql
        assert _attribute_maps(sql) in ([], ["attributes_string['event.name']"])
        assert set(re.findall(r"\bresource\.\S+", sql)) <= {"resource.`service.name`::String"}
        for column in ("status_message", "body", "events", "links"):
            assert not re.search(rf"\b{column}\b", sql)


def test_fingerprints_record_names_with_counts_and_save_atomically(tmp_path: Path) -> None:
    run = FakeClickHouse(fake_services())

    records = producers.fingerprint(run, ["toolbox"], 7)

    record = records["toolbox"]
    assert record["span_names"]["~/build"] == 5
    assert record["event_names"] == {"api_request": 12, "user_prompt": 4}
    assert record["attribute_keys"]["spans.string:tool.name"] == 40
    assert record["attribute_keys"]["logs.resource:service.name"] == 16
    assert record["truncated"] == {}
    directory = tmp_path / "fingerprints"
    path = producers.save_fingerprint(record, directory)
    assert path == directory / "toolbox.json"
    assert [item.name for item in directory.iterdir()] == ["toolbox.json"]
    loaded = producers.load_fingerprint(directory, "toolbox")
    assert loaded is not None
    assert {key: value for key, value in loaded.items() if key != "saved_at"} == record
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", loaded["saved_at"])


def test_a_service_that_cannot_name_a_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(producers.ProducerError, match="cannot name a fingerprint file"):
        producers.fingerprint_path(tmp_path, "../escape")
    with pytest.raises(producers.ProducerError, match="non-empty"):
        producers.fingerprint(FakeClickHouse({}), [" "], 7)


def _save_baseline(directory: Path, service: FakeService, name: str = "claude-code") -> None:
    baseline = producers.fingerprint(FakeClickHouse({name: service}), [name], 7)[name]
    producers.save_fingerprint(baseline, directory)


def test_drift_reports_added_and_removed_names_and_unregistered_services(tmp_path: Path) -> None:
    _save_baseline(
        tmp_path,
        FakeService(
            spans={"claude_code.interaction": 5, "claude_code.llm_request": 9},
            events={"api_request": 3},
            span_keys={("string", "session.id"): 5},
            span_rows=14,
        ),
    )
    now = fake_services()
    now["claude-code"] = FakeService(
        spans={"claude_code.interaction": 50, "claude_code.tool": 1},
        events={"api_request": 30},
        span_keys={("string", "session.id"): 50, ("number", "cost_usd"): 1},
        span_rows=51,
    )

    report = producers.drift_report(FakeClickHouse(now), tmp_path, ["claude-code", "codex_exec"])

    claude, codex = report["services"]
    assert claude["status"] == "drifted"
    assert claude["added"] == {
        "span_names": ["claude_code.tool"],
        "event_names": [],
        "attribute_keys": ["spans.number:cost_usd"],
    }
    assert claude["removed"] == {
        "span_names": ["claude_code.llm_request"],
        "event_names": [],
        "attribute_keys": [],
    }
    assert claude["saved_at"] is not None
    assert claude["days"] == 7
    assert (codex["service"], codex["status"], codex["saved_at"]) == (
        "codex_exec",
        "no_baseline",
        None,
    )
    assert report["unregistered"] == ["toolbox", "telemetrygen"]
    assert report["unnamed_rows"] == 0
    assert report["drifted"] is True


def test_changed_counts_alone_are_not_drift(tmp_path: Path) -> None:
    _save_baseline(tmp_path, FakeService(spans={"claude_code.interaction": 5}, span_rows=5))
    now = {"claude-code": FakeService(spans={"claude_code.interaction": 500}, span_rows=500)}

    report = producers.drift_report(FakeClickHouse(now), tmp_path, ["claude-code"])

    assert report["services"][0]["status"] == "unchanged"
    assert (report["unregistered"], report["drifted"]) == ([], False)


def test_drift_does_not_judge_removals_in_a_list_the_limit_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_baseline(tmp_path, FakeService(spans={"a": 1, "b": 1}, span_rows=2))
    monkeypatch.setattr(producers, "FINGERPRINT_LIMIT", 1)
    now = {"claude-code": FakeService(spans={"c": 9, "d": 8}, span_rows=17)}

    entry = producers.drift_report(FakeClickHouse(now), tmp_path, ["claude-code"])["services"][0]

    assert entry["added"]["span_names"] == ["c"]
    assert entry["removed"]["span_names"] == []
    assert entry["incomplete"] == ["span_names"]


def test_an_unreadable_baseline_is_reported_not_raised(tmp_path: Path) -> None:
    (tmp_path / "claude-code.json").write_text('{"schema": "other"}')
    (tmp_path / "oh-my-pi.json").write_text("{")

    report = producers.drift_report(FakeClickHouse({}), tmp_path, ["claude-code", "oh-my-pi"])

    statuses = [(entry["status"], entry["error"]) for entry in report["services"]]
    assert statuses[0] == (
        "invalid_baseline",
        f"claude-code.json is not a {producers.FINGERPRINT_SCHEMA} fingerprint",
    )
    assert statuses[1][0] == "invalid_baseline"
    assert "unreadable" in statuses[1][1]
    assert report["drifted"] is False

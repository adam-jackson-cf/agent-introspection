from __future__ import annotations

import copy
import dataclasses
import re
import tomllib
from collections.abc import Callable, Mapping
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest

from agent_introspection import capabilities, facts, profiles

_ALIGNMENT_OUTCOMES = {
    "aligned": capabilities.REACHABLE,
    "differs": capabilities.REACHABLE,
    "not applicable": capabilities.NOT_APPLICABLE,
    "not emitted": capabilities.UNREACHABLE,
}


def _document(key: str) -> dict[str, Any]:
    return tomllib.loads(files(profiles.PACKAGE).joinpath(f"{key}.toml").read_text())


def test_bundled_profiles_validate_and_cover_every_registry_harness() -> None:
    registry = facts.load_registry()

    loaded = profiles.load_profiles(registry)

    assert [profile.key for profile in loaded] == ["claude-code", "codex", "omp", "opencode"]
    registered = [profile for profile in loaded if profile.status == "registered"]
    covered = sorted(key for profile in registered for key in profile.registry_keys)
    assert covered == sorted(registry.harnesses)
    candidate = profiles.resolve("opencode", loaded)
    assert candidate.status == "candidate"
    assert candidate.registry_keys == ()
    assert candidate.settings == ()


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("omp", "omp"),
        ("oh-my-pi", "omp"),
        ("codex", "codex"),
        ("codex_exec", "codex"),
        ("codex-app-server", "codex"),
        ("codex_cli_rs", "codex"),
        ("claude-code", "claude-code"),
        ("opencode", "opencode"),
    ],
)
def test_resolve_accepts_a_profile_key_registry_key_or_service_name(name: str, key: str) -> None:
    assert profiles.resolve(name).key == key


def test_resolve_names_the_known_profiles_for_an_unknown_name() -> None:
    with pytest.raises(
        profiles.ProfileError, match=r"unknown harness profile gemini; known: .*omp"
    ):
        profiles.resolve("gemini")


def _surfaces() -> list[tuple[profiles.Profile, str]]:
    """Return each registered profile with each registry harness it covers."""
    return [(profile, key) for profile in profiles.load_profiles() for key in profile.registry_keys]


def _registered_cells() -> list[tuple[profiles.Profile, str, str, str]]:
    """Return every registry cell of a registered profile, with its alignment."""
    registry = facts.load_registry()
    cells = {(str(row["signal"]), str(row["harness"])): row for row in registry.support}
    return [
        (profile, key, signal, str(cells[signal, key]["alignment"]))
        for profile, key in _surfaces()
        for signal in registry.requires
    ]


def test_every_reached_registry_cell_has_its_required_capabilities_native_or_hook() -> None:
    registry = facts.load_registry()

    gaps = [
        (profile.key, key, signal, capability, status)
        for profile, key, signal, alignment in _registered_cells()
        if alignment in ("aligned", "differs")
        for capability, status in capabilities.statuses_for(
            profile.claims(key), signal, registry.requires[signal]
        ).items()
        if status not in capabilities.PRESENT
    ]

    assert gaps == []


def test_projection_of_every_registered_profile_matches_the_registry() -> None:
    registry = facts.load_registry()
    projections = {
        (profile.key, key): capabilities.project(profile, registry, key)
        for profile, key in _surfaces()
    }

    mismatches = [
        (profile.key, key, signal, alignment)
        for profile, key, signal, alignment in _registered_cells()
        if projections[profile.key, key][signal] != _ALIGNMENT_OUTCOMES[alignment]
    ]

    assert mismatches == []


def _claim(capability: str, **changes: Any) -> Callable[[dict[str, Any]], None]:
    def mutate(document: dict[str, Any]) -> None:
        document["capabilities"][capability] = {
            **document["capabilities"][capability],
            **changes,
        }

    return mutate


@pytest.mark.parametrize(
    ("key", "mutate", "message"),
    [
        ("omp", lambda d: d.update(schema="x/1"), "schema must be"),
        ("omp", lambda d: d.update(owner="x"), "unknown key owner"),
        ("omp", lambda d: d.update(status="draft"), "status must be one of"),
        ("omp", lambda d: d.update(registry_keys=["omp"]), "unknown: omp"),
        ("omp", lambda d: d.update(service_names=["omp"]), "service name its rows carry"),
        ("omp", lambda d: d.update(verified_versions=[]), "verified_versions must be a non-empty"),
        ("omp", lambda d: d.update(session_store="omp"), "unknown key session_store"),
        ("omp", lambda d: d.update(hook_adapter="gemini"), "hook_adapter must be"),
        ("omp", lambda d: d["capabilities"].pop("delegation"), "no claim for delegation"),
        ("omp", lambda d: d["capabilities"].update(cost={}), "unknown key cost"),
        ("omp", _claim("delegation", status="maybe"), "status must be one of"),
        ("omp", _claim("delegation", evidence=""), "evidence must be text"),
        ("omp", _claim("delegation", route="codex.agent_spawn"), "not a registry route"),
        ("omp", _claim("tool_arguments", status="native"), "status is hook"),
        ("omp", _claim("export", lacks=["usage.input_tokens"]), "does not require it"),
        (
            "omp",
            lambda d: d["setting"][0].update(value="{collector}"),
            "unknown placeholder collector",
        ),
        ("omp", lambda d: d["setting"][0].update(value=1.5), "value must be text"),
        ("omp", lambda d: d["setting"][0].update(file="settings"), "file must be one of env"),
        ("omp", lambda d: d["setting"][0].pop("value"), "value is required"),
        ("omp", lambda d: d["setting"].append(d["setting"][0]), "set twice in env"),
        ("omp", lambda d: d["setting"][0].update(group="labels"), "group must be one of"),
        ("omp", lambda d: d.update(setting=[]), "names the settings that switch on its export"),
        ("omp", lambda d: d["config_file"][0].update(table="otel"), "dotenv file has no tables"),
        ("omp", lambda d: d["config_file"][0].update(roots="all"), "roots must be one of"),
        ("omp", lambda d: d["config_file"].append(d["config_file"][0]), "duplicate id"),
        ("omp", lambda d: d.update(config_file={}), "array of tables"),
        (
            "omp",
            lambda d: d.update(capability_overrides={"codex_exec": {}}),
            "overrides for unknown surface codex_exec",
        ),
        ("claude-code", lambda d: d.update(hook_adapter=""), "a hook status needs a hook_adapter"),
        ("codex", lambda d: d.update(registry_keys=["codex"]), "unknown: codex"),
        ("opencode", lambda d: d.update(registry_keys=["opencode"]), "covers no registry harness"),
        ("opencode", lambda d: d.update(status="registered"), "registry_keys must name"),
    ],
)
def test_profiles_reject_documents_that_break_the_contract(
    key: str, mutate: Callable[[dict[str, Any]], object], message: str
) -> None:
    document = copy.deepcopy(_document(key))
    mutate(document)

    with pytest.raises(profiles.ProfileError, match=re.escape(message)):
        profiles.parse_profile(document, facts.load_registry())


def test_profile_names_must_be_unambiguous_and_cover_every_harness() -> None:
    registry = facts.load_registry()
    loaded = profiles.load_profiles(registry)
    by_key = {profile.key: profile for profile in loaded}

    with pytest.raises(profiles.ProfileError, match="omp name more than one"):
        profiles._check_profile_set([*loaded, by_key["omp"]], registry)
    with pytest.raises(profiles.ProfileError, match="no registered profile for oh-my-pi"):
        profiles._check_profile_set([by_key["claude-code"], by_key["codex"]], registry)


def test_load_profile_reads_a_draft_file_and_reports_its_path(tmp_path: Path) -> None:
    draft = tmp_path / "opencode.toml"
    draft.write_text(files(profiles.PACKAGE).joinpath("opencode.toml").read_text())
    broken = tmp_path / "broken.toml"
    broken.write_text("schema = ")

    assert profiles.load_profile(draft).key == "opencode"
    with pytest.raises(profiles.ProfileError, match=re.escape(str(broken))):
        profiles.load_profile(broken)


def test_codex_overrides_apply_only_to_the_headless_surface() -> None:
    codex = profiles.resolve("codex")

    assert codex.claims("codex_exec")["user_friction"].status == "not_applicable"
    assert codex.claims("codex_cli_rs")["user_friction"].status == "native"
    assert codex.claims()["user_friction"].status == "native"


def test_settings_render_their_placeholders_into_the_written_value() -> None:
    codex = profiles.resolve("codex")
    claude = profiles.resolve("claude-code")
    context = {
        "otlp_endpoint": "http://localhost:4318",
        "otlp_protocol": "http/protobuf",
        "service_name": claude.service_names[0],
    }
    by_key = {setting.key: setting for setting in [*codex.settings, *claude.settings]}

    assert by_key["exporter"].render(context) == {
        "otlp-http": {"endpoint": "http://localhost:4318/v1/logs", "protocol": "binary"}
    }
    assert by_key["log_user_prompt"].render(context) is True
    assert by_key["OTEL_SERVICE_NAME"].render(context) == "claude-code"
    assert {setting.group for setting in claude.settings} == {"export", "prompt_export"}
    with pytest.raises(profiles.ProfileError, match="no value for otlp_endpoint"):
        by_key["OTEL_EXPORTER_OTLP_ENDPOINT"].render({})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("$CONFIG_DIR/settings.json", "/srv/config/settings.json"),
        ("${CONFIG_DIR}/settings.json", "/srv/config/settings.json"),
        ("${CONFIG_DIR}x/$NAME.toml", "/srv/configx/codex.toml"),
        ("~/.codex/config.toml", "/home/user/.codex/config.toml"),
        ("~", "/home/user"),
        # `~` expands only as the first part; a glob pattern stays as written.
        ("/etc/~/a", "/etc/~/a"),
        ("~/$TILDE/config.toml", "/home/user/~/x/config.toml"),
        ("~/.codex/*/config.toml", "/home/user/.codex/*/config.toml"),
        # An unset or empty variable, in either spelling, rules the path out.
        ("$UNSET/settings.json", None),
        ("${UNSET}/settings.json", None),
        ("$EMPTY/settings.json", None),
        ("${CONFIG_DIR}/${EMPTY}", None),
    ],
)
def test_config_paths_expand_either_variable_spelling_and_a_leading_tilde(
    raw: str, expected: str | None
) -> None:
    environ = {"CONFIG_DIR": "/srv/config", "NAME": "codex", "TILDE": "~/x", "EMPTY": ""}

    expanded = profiles.expand_config_path(raw, environ, Path("/home/user"))

    assert expanded == (None if expected is None else Path(expected))


def test_export_context_fills_the_collector_endpoint_protocol_and_service() -> None:
    codex = profiles.resolve("codex")

    assert profiles.export_context(codex) == {
        "otlp_endpoint": "http://127.0.0.1:4318",
        "otlp_protocol": "http/protobuf",
        "service_name": "codex-app-server",
    }
    # The collector endpoint is OTLP/HTTP on any port (config refuses the gRPC one), and
    # an additional machine's is the host's.
    for endpoint in ("http://127.0.0.1:14318/", "http://studio.example:4318"):
        context = profiles.export_context(codex, endpoint)
        assert (context["otlp_endpoint"], context["otlp_protocol"]) == (
            endpoint.rstrip("/"),
            "http/protobuf",
        )


def _transport(
    key: str, setting_key: str, current: Mapping[str, object], endpoint: str | None = None
) -> str | None:
    profile = profiles.resolve(key)
    setting = next(item for item in profile.settings if item.key == setting_key)
    context = profiles.export_context(profile, endpoint)
    return profiles.equivalent_transport(profile, setting, current, context)


_GRPC = {"otlp-grpc": {"endpoint": "http://localhost:4317"}}
_ENDPOINT = "OTEL_EXPORTER_OTLP_ENDPOINT"
_PROTOCOL = "OTEL_EXPORTER_OTLP_PROTOCOL"


def _http(path: str, protocol: str = "binary", host: str = "127.0.0.1:4318") -> dict[str, object]:
    return {"otlp-http": {"endpoint": f"http://{host}{path}", "protocol": protocol}}


@pytest.mark.parametrize(
    ("key", "setting", "current", "expected"),
    [
        # Codex exporter tables: gRPC to 4317, or OTLP/HTTP to the same signal path.
        ("codex", "exporter", {"exporter": _GRPC}, "grpc"),
        ("codex", "trace_exporter", {"trace_exporter": _GRPC}, "grpc"),
        ("codex", "exporter", {"exporter": _http("/v1/logs", "json")}, "http"),
        ("codex", "exporter", {"exporter": _http("/v1/traces")}, None),
        ("codex", "exporter", {"exporter": _http("/v1/logs", "grpc")}, None),
        ("codex", "exporter", {"exporter": _http("/v1/logs", host="example.com:4318")}, None),
        ("codex", "exporter", {"exporter": _http("/v1/logs", host="localhost:99999")}, None),
        ("codex", "exporter", {"exporter": {"otlp-grpc": {"endpoint": "http://[::1]:4318"}}}, None),
        ("codex", "exporter", {"exporter": {**_GRPC, **_http("/v1/logs")}}, None),
        ("codex", "exporter", {"exporter": {"statsig": {}}}, None),
        ("codex", "exporter", {}, None),
        ("codex", "log_user_prompt", {"log_user_prompt": False}, None),
        # Claude Code's endpoint and protocol variables are judged as a pair.
        ("claude-code", _ENDPOINT, {_ENDPOINT: "http://localhost:4317"}, None),
        ("claude-code", _ENDPOINT, {_ENDPOINT: "http://localhost:4317", _PROTOCOL: "grpc"}, "grpc"),
        ("claude-code", _PROTOCOL, {_ENDPOINT: "http://localhost:4317", _PROTOCOL: "grpc"}, "grpc"),
        ("claude-code", _PROTOCOL, {_ENDPOINT: "http://localhost:4318", _PROTOCOL: "grpc"}, None),
        ("claude-code", _PROTOCOL, {_PROTOCOL: "http/json"}, "http"),
        (
            "claude-code",
            _ENDPOINT,
            {_ENDPOINT: "http://127.0.0.1:4318/v1/logs", _PROTOCOL: "http/protobuf"},
            None,
        ),
        ("claude-code", "OTEL_SERVICE_NAME", {"OTEL_SERVICE_NAME": "other"}, None),
        # omp exports OTLP/HTTP only, so a gRPC pair never satisfies its literal protocol.
        # The default names 127.0.0.1; localhost is the same machine.
        (
            "omp",
            _ENDPOINT,
            {_ENDPOINT: "http://localhost:4318", _PROTOCOL: "http/protobuf"},
            "http",
        ),
        ("omp", _ENDPOINT, {_ENDPOINT: "http://localhost:4317", _PROTOCOL: "grpc"}, None),
        ("omp", _PROTOCOL, {_PROTOCOL: "http/json"}, None),
    ],
)
def test_a_local_exporter_on_either_otlp_transport_satisfies_an_export_setting(
    key: str, setting: str, current: dict[str, object], expected: str | None
) -> None:
    assert _transport(key, setting, current) == expected


def test_a_protocol_without_an_endpoint_setting_is_judged_by_value_only() -> None:
    claude = profiles.resolve("claude-code")
    protocol = next(item for item in claude.settings if item.key == _PROTOCOL)
    alone = dataclasses.replace(claude, settings=(protocol,))
    context = profiles.export_context(claude)

    assert profiles.equivalent_transport(alone, protocol, {_PROTOCOL: "grpc"}, context) is None


def test_each_endpoint_variable_is_judged_by_its_own_signal_path() -> None:
    claude = profiles.resolve("claude-code")
    traces = profiles.Setting(
        file="settings",
        key="OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        value="{otlp_endpoint}/v1/traces",
        group="export",
        purpose="Per-signal endpoint.",
    )
    extended = dataclasses.replace(claude, settings=(*claude.settings, traces))
    context = profiles.export_context(claude)
    key = traces.key

    def judged(url: str, protocol: str) -> str | None:
        current = {key: url, _PROTOCOL: protocol}
        return profiles.equivalent_transport(extended, traces, current, context)

    assert judged("http://127.0.0.1:4318/v1/traces", "http/json") == "http"
    assert judged("http://127.0.0.1:4318/v1/logs", "http/json") is None
    assert judged("http://localhost:4317", "grpc") == "grpc"


def test_otlp_http_must_reach_the_configured_collector_port() -> None:
    exporter = {"exporter": _http("/v1/logs", host="localhost:4318")}

    assert _transport("codex", "exporter", exporter, "http://localhost:14318") is None
    moved = {"exporter": _http("/v1/logs", host="localhost:14318")}
    assert _transport("codex", "exporter", moved, "http://localhost:14318") == "http"
    assert _transport("codex", "exporter", {"exporter": _GRPC}, "http://localhost:14318") == "grpc"


def test_an_additional_machines_exporters_reach_the_collector_only_by_the_hosts_name() -> None:
    host = "http://studio.example:4318"
    to_host = {"otlp-grpc": {"endpoint": "http://Studio.example:4317"}}

    assert _transport("codex", "exporter", {"exporter": to_host}, host) == "grpc"
    on_host = {"exporter": _http("/v1/logs", host="studio.example:4318")}
    assert _transport("codex", "exporter", on_host, host) == "http"
    elsewhere = {"exporter": _http("/v1/logs", host="other.example:4318")}
    assert _transport("codex", "exporter", elsewhere, host) is None
    # A local exporter on an additional machine reaches no collector.
    assert _transport("codex", "exporter", {"exporter": _GRPC}, host) is None
    pair = {_ENDPOINT: "http://studio.example:4317", _PROTOCOL: "grpc"}
    assert _transport("claude-code", _ENDPOINT, pair, host) == "grpc"
    assert profiles.on_collector_host("http://localhost:4318", "http://127.0.0.1:4318")
    assert not profiles.on_collector_host("http://localhost:4318", host)


def test_home_globs_add_every_matching_codex_home(tmp_path: Path) -> None:
    spec = next(item for item in profiles.resolve("codex").config_files if item.id == "config")
    homes = [
        tmp_path / "Library/Application Support/orca/codex-accounts" / name / "home"
        for name in ("b", "a")
    ]
    for home in homes:
        home.mkdir(parents=True)
    (tmp_path / ".codex").mkdir()

    found = profiles.config_paths(spec, {}, tmp_path)

    assert found == [
        tmp_path / ".codex/config.toml",
        homes[1] / "config.toml",
        homes[0] / "config.toml",
    ]


def test_home_globs_never_replace_the_default_codex_home(tmp_path: Path) -> None:
    spec = next(item for item in profiles.resolve("codex").config_files if item.id == "config")
    orca = tmp_path / "Library/Application Support/orca/codex-accounts/a/home"
    orca.mkdir(parents=True)

    found = profiles.config_paths(spec, {}, tmp_path)

    assert found == [tmp_path / ".codex/config.toml", orca / "config.toml"]


def test_home_globs_need_a_file_kept_in_each_root() -> None:
    document = copy.deepcopy(_document("omp"))
    document["config_file"][0]["home_globs"] = ["~/x/*"]

    with pytest.raises(profiles.ProfileError, match="home_globs needs roots = each"):
        profiles.parse_profile(document, facts.load_registry())

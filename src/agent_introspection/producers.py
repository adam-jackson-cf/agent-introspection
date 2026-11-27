from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any, TypedDict

from agent_introspection import capabilities, facts, hooks, preflight, profiles, spool
from agent_introspection.capabilities import Capability, CapabilityClaim

SPAN_TABLE = ".".join(preflight.SPAN_TABLE)
LOG_TABLE = ".".join(preflight.LOG_TABLE)
LOG_SERVICE = "resource.`service.name`::String"
QUERY_SECONDS = 120
_SETTINGS = f"SETTINGS max_execution_time = {QUERY_SECONDS}"
DEFAULT_DAYS = 7
NAME_LIMIT = 200
FINGERPRINT_LIMIT = 5000
EVIDENCE_LIMIT = 10
FINGERPRINT_SCHEMA = "agent-introspection.producer-fingerprint/1"
FINGERPRINT_KINDS = ("span_names", "event_names", "attribute_keys")
CLASSES = ("registered", "known_producer", "internal", "unregistered")
EMPTY_SERVICE = "(empty)"
EMPTY_SERVICE_NOTE = (
    "Rows with an empty service.name: their sender names no service, so `producers "
    "profile` cannot select them. Set the sender's OTEL_SERVICE_NAME (or its service.name "
    "resource attribute)."
)
SOURCES = ("spans", "logs")
_TABLES = {"spans": SPAN_TABLE, "logs": LOG_TABLE}
_KEY_MAPS = (
    ("string", "attributes_string"),
    ("number", "attributes_number"),
    ("bool", "attributes_bool"),
    ("resource", "resources_string"),
)
KEY_KINDS = tuple(kind for kind, _column in _KEY_MAPS)
KEY_FAMILIES = tuple(f"{source}.{kind}" for source in SOURCES for kind in KEY_KINDS)
_KEY_ENTRIES = ", ".join(
    f"arrayMap(k -> ('{kind}', toString(k)), mapKeys({column}))" for kind, column in _KEY_MAPS
)
_CONTENT_WORDS = frozenset({
    "arg",
    "args",
    "argument",
    "arguments",
    "body",
    "cmd",
    "command",
    "completion",
    "content",
    "contents",
    "diff",
    "input",
    "inputs",
    "message",
    "messages",
    "output",
    "outputs",
    "patch",
    "prompt",
    "prompts",
    "query",
    "statement",
    "stderr",
    "stdout",
    "text",
})
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_KEY_SEPARATORS = re.compile(r"[^a-z0-9]+")
_SERVICE_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
_HINT_LABELS = {"span_names": "span", "event_names": "event", "attribute_keys": "key"}
CONTENT_NOTE = (
    "Key names that suggest user text (prompts, commands, tool arguments or output, "
    "message bodies, query text); their values were not read."
)
GUESS_NOTE = (
    "A guess from hint matches on names, not a verification: native when a hint "
    "matched, else unverified. Check each claim against its capability's check."
)
DRAFT_TO_COMPLETE = {
    "binary": "the producer's command",
    "version_args": "the arguments that print its version, such as --version",
    "verified_versions": "the versions the claims were checked on",
}
_DRAFT_HEADER = (
    "Draft harness profile written by `agent-introspection producers profile`.",
    "Every capability status is a guess from span, event, and attribute key names;",
    "no attribute value was read. Check each claim, fill the fields marked",
    "`complete:`, then preview it with `agent-introspection harness impact <this file>`.",
)


class ProducerError(ValueError):
    pass


class NameCount(TypedDict):
    name: str
    rows: int


class NameList(TypedDict):
    names: list[NameCount]
    distinct: int
    dropped: int


@dataclass(frozen=True)
class Telemetry:
    span_names: NameList
    event_names: NameList
    keys: dict[str, NameList]


def _check_days(days: int) -> int:
    if days < 1:
        raise ProducerError("days must be at least 1")
    return int(days)


def _check_service(service: str) -> str:
    if not service.strip():
        raise ProducerError("service must be a non-empty service.name")
    return service


def _rows(run: facts.SqlRunner, sql: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in run(sql).splitlines() if line.strip()]


def _iso(nanoseconds: int) -> str:
    moment = datetime.fromtimestamp(nanoseconds // 1_000_000_000, UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _span_window(days: int) -> str:
    return f"timestamp > now() - INTERVAL {days} DAY"


def _log_window(days: int) -> str:
    return f"timestamp > toUInt64(toUnixTimestamp(now() - INTERVAL {days} DAY)) * 1000000000"


def _where(source: str, service: str, days: int) -> str:
    if source == "spans":
        return f"serviceName = {facts.sql_string(service)} AND {_span_window(days)}"
    return f"{LOG_SERVICE} = {facts.sql_string(service)} AND {_log_window(days)}"


def activity_sql(days: int, service: str | None = None) -> str:
    spans_only = "" if service is None else f" AND serviceName = {facts.sql_string(service)}"
    logs_only = "" if service is None else f" AND {LOG_SERVICE} = {facts.sql_string(service)}"
    return (
        "SELECT service, sum(span_rows) AS span_rows, sum(log_rows) AS log_rows, "
        "min(first_ns) AS first_ns, max(last_ns) AS last_ns FROM ("
        "SELECT serviceName AS service, count() AS span_rows, toUInt64(0) AS log_rows, "
        "toUnixTimestamp64Nano(min(timestamp)) AS first_ns, "
        "toUnixTimestamp64Nano(max(timestamp)) AS last_ns "
        f"FROM {SPAN_TABLE} WHERE {_span_window(days)}{spans_only} GROUP BY service "
        f"UNION ALL SELECT {LOG_SERVICE} AS service, toUInt64(0), count(), "
        "toInt64(min(timestamp)), toInt64(max(timestamp)) "
        f"FROM {LOG_TABLE} WHERE {_log_window(days)}{logs_only} GROUP BY service"
        f") GROUP BY service {_SETTINGS} FORMAT JSONEachRow"
    )


def _top_names(inner: str, limit: int) -> str:
    return (
        f"SELECT name, rows, count() OVER () AS distinct_names FROM ({inner}) "
        f"ORDER BY rows DESC, name LIMIT {limit} {_SETTINGS} FORMAT JSONEachRow"
    )


def span_names_sql(service: str, days: int, limit: int) -> str:
    return _top_names(
        f"SELECT name, count() AS rows FROM {SPAN_TABLE} "
        f"WHERE {_where('spans', service, days)} GROUP BY name",
        limit,
    )


def event_names_sql(service: str, days: int, limit: int) -> str:
    return _top_names(
        f"SELECT attributes_string['event.name'] AS name, count() AS rows FROM {LOG_TABLE} "
        f"WHERE {_where('logs', service, days)} AND name != '' GROUP BY name",
        limit,
    )


def keys_sql(source: str, service: str, days: int, limit: int) -> str:
    return (
        "SELECT family, key, rows, count() OVER (PARTITION BY family) AS distinct_names FROM ("
        f"SELECT entry.1 AS family, entry.2 AS key, count() AS rows FROM {_TABLES[source]} "
        f"ARRAY JOIN arrayConcat({_KEY_ENTRIES}) AS entry "
        f"WHERE {_where(source, service, days)} GROUP BY family, key"
        f") ORDER BY rows DESC, key LIMIT {limit} BY family {_SETTINGS} FORMAT JSONEachRow"
    )


def _activity(run: facts.SqlRunner, days: int, service: str | None = None) -> list[dict[str, Any]]:
    return [
        {
            "service": str(row["service"]),
            "span_rows": int(row["span_rows"]),
            "log_rows": int(row["log_rows"]),
            "first_seen": _iso(int(row["first_ns"])),
            "last_seen": _iso(int(row["last_ns"])),
        }
        for row in _rows(run, activity_sql(days, service))
    ]


def service_class(service: str, registry: facts.Registry) -> str:
    if service in registry.harnesses:
        return "registered"
    if service in registry.producers:
        return "known_producer"
    if service == spool.SERVICE_NAME:
        return "internal"
    return "unregistered"


def _profile_key(service: str, known: Sequence[profiles.Profile]) -> str | None:
    try:
        return profiles.resolve(service, known).key
    except profiles.ProfileError:
        return None


def _empty_service(entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "service": EMPTY_SERVICE,
        "span_rows": sum(int(entry["span_rows"]) for entry in entries),
        "log_rows": sum(int(entry["log_rows"]) for entry in entries),
        "first_seen": min(str(entry["first_seen"]) for entry in entries),
        "last_seen": max(str(entry["last_seen"]) for entry in entries),
        "class": "unregistered",
        "enabled": None,
        "profile": None,
        "note": EMPTY_SERVICE_NOTE,
    }


def _volume_order(entry: Mapping[str, Any]) -> tuple[int, int, str]:
    rows = int(entry["span_rows"]) + int(entry["log_rows"])
    return CLASSES.index(str(entry["class"])), -rows, str(entry["service"])


def discover(
    run: facts.SqlRunner,
    registry: facts.Registry,
    enabled: Sequence[str] | None,
    days: int,
) -> dict[str, Any]:
    days = _check_days(days)
    harnesses = facts.enabled_harnesses(registry, enabled)
    known = profiles.load_profiles(registry)
    services = []
    blank = []
    for entry in _activity(run, days):
        service = entry["service"]
        if not service.strip():
            blank.append(entry)
            continue
        kind = service_class(service, registry)
        services.append({
            **entry,
            "service": hooks.redact_home(service),
            "class": kind,
            "enabled": service in harnesses if kind == "registered" else None,
            "profile": _profile_key(service, known),
        })
    if blank:
        services.append(_empty_service(blank))
    services.sort(key=_volume_order)
    seen = {entry["service"] for entry in services}
    return {
        "days": days,
        "services": services,
        "enabled_not_seen": [harness for harness in harnesses if harness not in seen],
    }


def _name_list(rows: Sequence[Mapping[str, Any]], field: str = "name") -> NameList:
    counts: dict[str, int] = {}
    for row in rows:
        name = hooks.redact_home(str(row[field]))
        counts[name] = counts.get(name, 0) + int(row["rows"])
    distinct = max((int(row["distinct_names"]) for row in rows), default=0)
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return {
        "names": [{"name": name, "rows": count} for name, count in ordered],
        "distinct": distinct,
        "dropped": max(distinct - len(rows), 0),
    }


def _key_lists(
    run: facts.SqlRunner, source: str, service: str, days: int, limit: int
) -> dict[str, NameList]:
    by_family: dict[str, list[dict[str, Any]]] = {}
    for row in _rows(run, keys_sql(source, service, days, limit)):
        by_family.setdefault(f"{source}.{row['family']}", []).append(row)
    return {family: _name_list(rows, "key") for family, rows in by_family.items()}


def collect(run: facts.SqlRunner, service: str, days: int, limit: int) -> Telemetry:
    keys: dict[str, NameList] = {
        family: {"names": [], "distinct": 0, "dropped": 0} for family in KEY_FAMILIES
    }
    for source in SOURCES:
        keys.update(_key_lists(run, source, service, days, limit))
    return Telemetry(
        span_names=_name_list(_rows(run, span_names_sql(service, days, limit))),
        event_names=_name_list(_rows(run, event_names_sql(service, days, limit))),
        keys=keys,
    )


def is_content_key(key: str) -> bool:
    words = [word for word in _KEY_SEPARATORS.split(_CAMEL_BOUNDARY.sub("_", key).lower()) if word]
    while words and words[-1].isdigit():
        words.pop()
    return bool(words) and words[-1] in _CONTENT_WORDS


def content_keys(telemetry: Telemetry) -> dict[str, Any]:
    flagged = [
        {"family": family, "key": entry["name"], "rows": entry["rows"]}
        for family in (f"{source}.string" for source in SOURCES)
        for entry in telemetry.keys[family]["names"]
        if is_content_key(entry["name"])
    ]
    return {"note": CONTENT_NOTE, "keys": flagged}


def _counts(names: NameList) -> dict[str, int]:
    return {entry["name"]: entry["rows"] for entry in names["names"]}


def _hint_names(telemetry: Telemetry) -> dict[str, dict[str, int]]:
    attribute_keys: dict[str, int] = {}
    for names in telemetry.keys.values():
        for entry in names["names"]:
            attribute_keys[entry["name"]] = attribute_keys.get(entry["name"], 0) + entry["rows"]
    return {
        "span_names": _counts(telemetry.span_names),
        "event_names": _counts(telemetry.event_names),
        "attribute_keys": attribute_keys,
    }


def _guess(capability: Capability, names: Mapping[str, Mapping[str, int]]) -> dict[str, Any]:
    matches = sorted(
        (-rows, kind, name)
        for kind, found in names.items()
        for name, rows in found.items()
        if capability.matches(kind, name)
    )
    return {
        "id": capability.id,
        "title": capability.title,
        "guess": "native" if matches else "unverified",
        "evidence": [
            {"kind": kind, "name": name, "rows": -rows}
            for rows, kind, name in matches[:EVIDENCE_LIMIT]
        ],
        "more_evidence": max(len(matches) - EVIDENCE_LIMIT, 0),
    }


def guess_capabilities(
    telemetry: Telemetry, known: Iterable[Capability] | None = None
) -> list[dict[str, Any]]:
    names = _hint_names(telemetry)
    return [_guess(capability, names) for capability in known or capabilities.load_capabilities()]


def draft_key(service: str) -> str:
    key = _KEY_SEPARATORS.sub("-", service.lower()).strip("-")
    if not key:
        raise ProducerError(f"cannot derive a profile key from service {service!r}")
    return key


def _claim_evidence(guess: Mapping[str, Any], days: int) -> str:
    shown = [
        f"{_HINT_LABELS[item['kind']]} `{item['name']}` ({item['rows']} rows)"
        for item in guess["evidence"][:3]
    ]
    if not shown:
        return f"Unverified: no hint matched this service's names over {days} days."
    return f"Guessed from names over {days} days, not verified: {', '.join(shown)}."


def draft_profile(service: str, guesses: Sequence[Mapping[str, Any]], days: int) -> dict[str, Any]:
    return {
        "schema": profiles.SCHEMA,
        "key": draft_key(service),
        "title": hooks.redact_home(service),
        "status": "candidate",
        "registry_keys": [],
        "service_names": [service],
        "binary": "",
        "version_args": [],
        "verified_versions": [],
        "hook_adapter": "",
        "capabilities": {
            str(guess["id"]): {"status": guess["guess"], "evidence": _claim_evidence(guess, days)}
            for guess in guesses
        },
    }


def check_draft(draft: Mapping[str, Any], registry: facts.Registry) -> dict[str, Any]:
    try:
        profiles.parse_profile(draft, registry, "draft")
    except profiles.ProfileError as error:
        return {"valid": False, "error": str(error), "to_complete": dict(DRAFT_TO_COMPLETE)}
    return {"valid": True, "error": None, "to_complete": {}}


def draft_candidate(draft: Mapping[str, Any]) -> profiles.Profile:
    return profiles.Profile(
        key=str(draft["key"]),
        title=str(draft["title"]),
        status="candidate",
        registry_keys=(),
        service_names=tuple(draft["service_names"]),
        binary=str(draft["binary"]),
        version_args=tuple(draft["version_args"]),
        verified_versions=tuple(draft["verified_versions"]),
        hook_adapter=str(draft["hook_adapter"]),
        capabilities={
            capability: CapabilityClaim(status=claim["status"], evidence=claim["evidence"])
            for capability, claim in draft["capabilities"].items()
        },
    )


def _totals(activity: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    fields = ("span_rows", "log_rows", "first_seen", "last_seen")
    if not activity:
        return {"span_rows": 0, "log_rows": 0, "first_seen": None, "last_seen": None}
    return {field: activity[0][field] for field in fields}


def profile(
    run: facts.SqlRunner,
    service: str,
    days: int,
    registry: facts.Registry,
    *,
    enabled: Sequence[str] | None = None,
) -> dict[str, Any]:
    days = _check_days(days)
    telemetry = collect(run, _check_service(service), days, NAME_LIMIT)
    guesses = guess_capabilities(telemetry)
    draft = draft_profile(service, guesses, days)
    candidate = draft_candidate(draft)
    return {
        "service": hooks.redact_home(service),
        "days": days,
        "class": service_class(service, registry),
        "profile": _profile_key(service, profiles.load_profiles(registry)),
        **_totals(_activity(run, days, service)),
        "span_names": telemetry.span_names,
        "event_names": telemetry.event_names,
        "resource_keys": {source: telemetry.keys[f"{source}.resource"] for source in SOURCES},
        "attribute_keys": {
            family: names
            for family, names in telemetry.keys.items()
            if not family.endswith(".resource")
        },
        "content_keys": content_keys(telemetry),
        "capabilities": {"note": GUESS_NOTE, "guesses": guesses},
        "draft": draft,
        "draft_check": check_draft(draft, registry),
        "projection": capabilities.project(candidate, registry),
        "impact": capabilities.impact(enabled, candidate, registry),
    }


def _name_lists(result: Mapping[str, Any]) -> list[tuple[str, NameList]]:
    lists = [("span names", result["span_names"]), ("event names", result["event_names"])]
    lists += [(f"{family} keys", names) for family, names in result["attribute_keys"].items()]
    lists += [
        (f"{source}.resource keys", names) for source, names in result["resource_keys"].items()
    ]
    return lists


def truncation_notes(result: Mapping[str, Any]) -> list[str]:
    return [
        f"{label}: kept the top {len(names['names'])} of {names['distinct']} by rows; "
        f"{names['dropped']} dropped"
        for label, names in _name_lists(result)
        if names["dropped"]
    ]


def _toml_string(text: str) -> str:
    return json.dumps(text, ensure_ascii=False).replace("\x7f", "\\u007f")


def _toml_key(key: str) -> str:
    return key if _BARE_KEY.fullmatch(key) else _toml_string(key)


def toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    if isinstance(value, Mapping):
        entries = ", ".join(
            f"{_toml_key(str(key))} = {toml_value(item)}" for key, item in value.items()
        )
        return "{ " + entries + " }" if entries else "{}"
    raise ProducerError(f"cannot write a {type(value).__name__} as TOML")


def render_draft(draft: Mapping[str, Any]) -> str:
    lines = [f"# {line}" for line in _DRAFT_HEADER]
    tables = {key: value for key, value in draft.items() if isinstance(value, Mapping)}
    for key, value in draft.items():
        if key not in tables:
            note = DRAFT_TO_COMPLETE.get(key)
            comment = f"  # complete: {note}" if note else ""
            lines.append(f"{_toml_key(key)} = {toml_value(value)}{comment}")
    for key, table in tables.items():
        lines += ["", f"[{_toml_key(key)}]"]
        lines += [f"{_toml_key(str(name))} = {toml_value(item)}" for name, item in table.items()]
    return "\n".join(lines) + "\n"


def draft_target(path: Path, *, force: bool = False) -> Path:
    target = path.expanduser().resolve()
    bundled = Path(str(files(profiles.PACKAGE))).resolve()
    if target == bundled or bundled in target.parents:
        raise ProducerError(
            f"refusing to write inside the bundled profiles ({hooks.redact_home(str(bundled))});"
            " write the draft elsewhere and review it first"
        )
    if target.is_dir():
        raise ProducerError(f"{path} is a directory")
    if target.exists() and not force:
        raise ProducerError(f"{path} exists; pass --force to overwrite it")
    if not target.parent.is_dir():
        raise ProducerError(f"{target.parent} is not a directory")
    return target


def write_draft(draft: Mapping[str, Any], path: Path, *, force: bool = False) -> Path:
    target = draft_target(path, force=force)
    hooks.atomic_write(target, render_draft(draft))
    return target


def fingerprint_dir() -> Path:
    return hooks.data_dir() / "fingerprints"


def _fingerprint_of(telemetry: Telemetry, service: str, days: int) -> dict[str, Any]:
    dropped = {
        "span_names": telemetry.span_names["dropped"],
        "event_names": telemetry.event_names["dropped"],
        "attribute_keys": sum(names["dropped"] for names in telemetry.keys.values()),
    }
    return {
        "schema": FINGERPRINT_SCHEMA,
        "service": service,
        "days": days,
        "span_names": _counts(telemetry.span_names),
        "event_names": _counts(telemetry.event_names),
        "attribute_keys": {
            f"{family}:{name}": rows
            for family, names in telemetry.keys.items()
            for name, rows in _counts(names).items()
        },
        "truncated": {kind: count for kind, count in dropped.items() if count},
    }


def fingerprint(
    run: facts.SqlRunner, services: Sequence[str], days: int
) -> dict[str, dict[str, Any]]:
    days = _check_days(days)
    return {
        service: _fingerprint_of(
            collect(run, _check_service(service), days, FINGERPRINT_LIMIT), service, days
        )
        for service in services
    }


def fingerprint_path(directory: Path, service: str) -> Path:
    if not _SERVICE_FILE.fullmatch(service):
        raise ProducerError(f"service {service!r} cannot name a fingerprint file")
    return directory / f"{service}.json"


def save_fingerprint(record: Mapping[str, Any], directory: Path) -> Path:
    path = fingerprint_path(directory, str(record["service"]))
    directory.mkdir(parents=True, exist_ok=True)
    saved_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    hooks.atomic_write(path, json.dumps({**record, "saved_at": saved_at}, indent=2) + "\n")
    return path


def load_fingerprint(directory: Path, service: str) -> dict[str, Any] | None:
    path = fingerprint_path(directory, service)
    if not path.is_file():
        return None
    try:
        saved = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ProducerError(f"{path.name} is unreadable: {error}") from None
    valid = (
        isinstance(saved, dict)
        and saved.get("schema") == FINGERPRINT_SCHEMA
        and isinstance(saved.get("days"), int)
        and all(isinstance(saved.get(kind), dict) for kind in FINGERPRINT_KINDS)
    )
    if not valid:
        raise ProducerError(f"{path.name} is not a {FINGERPRINT_SCHEMA} fingerprint")
    return dict(saved)


def _no_names() -> dict[str, list[str]]:
    return {kind: [] for kind in FINGERPRINT_KINDS}


def _compare(saved: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    added = {
        kind: []
        if kind in saved.get("truncated", {})
        else sorted(set(current[kind]) - set(saved[kind]))
        for kind in FINGERPRINT_KINDS
    }
    removed = {
        kind: [] if kind in current["truncated"] else sorted(set(saved[kind]) - set(current[kind]))
        for kind in FINGERPRINT_KINDS
    }
    changed = any(added.values()) or any(removed.values())
    return {
        "status": "drifted" if changed else "unchanged",
        "added": added,
        "removed": removed,
        "incomplete": sorted({*saved.get("truncated", {}), *current["truncated"]}),
    }


def _drift(run: facts.SqlRunner, saved_dir: Path, service: str) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "service": service,
        "status": "no_baseline",
        "saved_at": None,
        "days": None,
        "added": _no_names(),
        "removed": _no_names(),
        "incomplete": [],
        "error": None,
    }
    try:
        saved = load_fingerprint(saved_dir, service)
    except ProducerError as error:
        return {**entry, "status": "invalid_baseline", "error": str(error)}
    if saved is None:
        return entry
    days = int(saved["days"])
    current = _fingerprint_of(collect(run, service, days, FINGERPRINT_LIMIT), service, days)
    return {**entry, "saved_at": saved.get("saved_at"), "days": days, **_compare(saved, current)}


def drift_report(run: facts.SqlRunner, saved_dir: Path, services: Sequence[str]) -> dict[str, Any]:
    entries = [_drift(run, saved_dir, service) for service in services]
    found = discover(run, facts.load_registry(), None, DEFAULT_DAYS)["services"]
    unregistered = [entry for entry in found if entry["class"] == "unregistered"]
    unnamed = [entry for entry in unregistered if entry["service"] == EMPTY_SERVICE]
    return {
        "services": entries,
        "unregistered": [
            entry["service"] for entry in unregistered if entry["service"] != EMPTY_SERVICE
        ],
        "unnamed_rows": sum(entry["span_rows"] + entry["log_rows"] for entry in unnamed),
        "drifted": any(entry["status"] == "drifted" for entry in entries),
    }

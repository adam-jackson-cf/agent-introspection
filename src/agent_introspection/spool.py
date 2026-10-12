"""The record spool and its one route to the facts store: OTLP/HTTP logs.

Every machine writes its records (activity-hook events, session attribution, spool
status) as one JSON file each into the spool, ``hooks.INBOX``. ``drain`` ships them
in batches of at most ``BATCH_SIZE`` to the logs endpoint (``[hooks]
otlp_logs_endpoint``, else the OTEL environment), where SigNoz keeps them and the
``facts`` loaders read them back into ClickHouse. ``sync`` is the whole periodic step:
attribute new sessions, record a status heartbeat, drain.

A file leaves the spool only once the collector accepted it. A batch the collector
reports as rejected (a partial success, or a 400 carrying an OTLP status) moves to
``rejected/``: it may already be partly stored, and resending is safe because the loaders
deduplicate on ``event_id``. A response that says the endpoint is wrong (404, 415, other
4xx) or a transient failure (429, 5xx, timeout, refused) keeps the files and records
its class. Only exception and class names are logged, never URLs or record text.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import http.client
import json
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Generator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import metadata
from itertools import islice
from pathlib import Path
from typing import Any, Literal

from agent_introspection import hooks
from agent_introspection.config import AppConfig, load_config, resolve_otlp_logs_endpoint

SESSION_PROJECT_SCHEMA = "agent-introspection.session-project/1"
STATUS_SCHEMA = "agent-introspection.spool-status/1"
SERVICE_NAME = "agent-introspection"
SCOPE_NAME = "agent-introspection.spool"
BATCH_SIZE = 50
CONNECT_TIMEOUT_SECONDS = 3
DRAIN_BUDGET_SECONDS = 20
_MAX_RESPONSE_BYTES = 64 * 1024
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

type Record = dict[str, Any]


@dataclass(frozen=True)
class Family:
    """One record family: its OTLP ``event.name``, short name, and record-time field."""

    name: str
    event_name: str
    time_field: str


FAMILIES: dict[str, Family] = {
    hooks.SCHEMA: Family("hook-event", "agent_introspection.hook_event", "occurred_at"),
    SESSION_PROJECT_SCHEMA: Family(
        "session-project", "agent_introspection.session_project", "resolved_at"
    ),
    STATUS_SCHEMA: Family("spool-status", "agent_introspection.spool_status", "reported_at"),
}
_COUNTED = ("hook-event", "session-project")


def host_name() -> str:
    """Return this machine's short host name."""
    return socket.gethostname().split(".")[0]


def cli_version() -> str:
    """Return the installed package version, or ``unknown`` when run from a bare checkout."""
    try:
        return metadata.version("agent-introspection")
    except metadata.PackageNotFoundError:
        return "unknown"


def _directory(inbox: Path | None) -> Path:
    return inbox if inbox is not None else hooks.INBOX


def _state_path(inbox: Path) -> Path:
    return inbox.parent / "spool-state.json"


def pending_paths(inbox: Path | None = None, limit: int | None = None) -> list[Path]:
    """Return the spooled record files (at most ``limit``, in directory order)."""
    directory = _directory(inbox)
    return list(islice(directory.glob("*.json"), limit)) if directory.is_dir() else []


def backlog(inbox: Path | None = None) -> int:
    """Return how many record files wait in the spool."""
    return len(pending_paths(inbox))


def rejected_count(inbox: Path | None = None) -> int:
    """Return how many record files were quarantined in ``rejected/``."""
    quarantine = _directory(inbox) / "rejected"
    return len(list(quarantine.glob("*.json"))) if quarantine.is_dir() else 0


# --- State ------------------------------------------------------------------------


def read_state(inbox: Path | None = None) -> dict[str, Any]:
    """Return the persisted send counters: ``sent_total`` per family and ``last_error_class``."""
    state: dict[str, Any] = {"sent_total": {}, "last_error_class": ""}
    try:
        document = json.loads(_state_path(_directory(inbox)).read_text())
        sent = document["sent_total"]
        state["sent_total"] = {
            name: sent[name] for name in _COUNTED if isinstance(sent.get(name), int)
        }
        error = document["last_error_class"]
        state["last_error_class"] = error if isinstance(error, str) else ""
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        pass
    return state


def _save_state(inbox: Path, state: Mapping[str, Any]) -> None:
    hooks.atomic_write(_state_path(inbox), json.dumps(state, separators=(",", ":")) + "\n")


# --- OTLP batches -----------------------------------------------------------------


def _time_ns(value: str) -> int:
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return (moment - _EPOCH) // timedelta(microseconds=1) * 1000


def _attribute(key: str, value: str) -> dict[str, Any]:
    return {"key": key, "value": {"stringValue": value}}


def build_body(records: list[Record]) -> bytes:
    """Return the OTLP/JSON logs request for ``records``.

    Each record is one log record: ``timeUnixNano`` is its own time (the collector
    stamps the observed time, which the loaders window on), the body is the record's
    compact JSON, and ``event.name`` plus the schema select its family downstream.
    """
    entries = []
    for item in records:
        family = FAMILIES[item["schema"]]
        entries.append({
            "timeUnixNano": str(_time_ns(item[family.time_field])),
            "severityText": "INFO",
            "body": {"stringValue": json.dumps(item, separators=(",", ":"), ensure_ascii=False)},
            "attributes": [
                _attribute("event.name", family.event_name),
                _attribute("agent_introspection.schema", item["schema"]),
                _attribute("agent_introspection.event_id", item["event_id"]),
            ],
        })
    document = {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [
                        _attribute("service.name", SERVICE_NAME),
                        _attribute("host.name", host_name()),
                        _attribute("agent_introspection.cli_version", cli_version()),
                    ]
                },
                "scopeLogs": [{"scope": {"name": SCOPE_NAME}, "logRecords": entries}],
            }
        ]
    }
    return json.dumps(document, separators=(",", ":")).encode()


# --- Responses --------------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    """What to do with a sent batch's files, and the error class to record."""

    action: Literal["delete", "reject", "keep"]
    error_class: str = ""


def _rejected_records(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _is_otlp_status(text: bytes) -> bool:
    try:
        document = json.loads(text)
    except ValueError:
        return False
    return isinstance(document, dict) and "code" in document and "message" in document


def _classify_success(text: bytes) -> Outcome:
    if not text:
        return Outcome("delete")
    try:
        document = json.loads(text)
    except ValueError:
        return Outcome("keep", "unparseable_response")
    partial = document.get("partialSuccess", {}) if isinstance(document, dict) else None
    rejected = (
        _rejected_records(partial.get("rejectedLogRecords", 0))
        if isinstance(partial, dict)
        else None
    )
    if rejected is None:
        return Outcome("keep", "unparseable_response")
    return Outcome("reject", "partial_success") if rejected > 0 else Outcome("delete")


def classify_response(status: int, body: bytes) -> Outcome:
    """Classify one collector response (see the module docstring for the classes)."""
    text = body.strip()
    if 200 <= status < 300:
        return _classify_success(text)
    if status == 400 and _is_otlp_status(text):
        return Outcome("reject", "rejected_batch")
    if status == 429 or status >= 500:
        return Outcome("keep", f"http_{status}")
    return Outcome("keep", "endpoint_misconfigured")


def send(endpoint: str, body: bytes, timeout: float = CONNECT_TIMEOUT_SECONDS) -> Outcome:
    """POST one OTLP/JSON request and classify the answer; never raises."""
    request = urllib.request.Request(
        endpoint, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return classify_response(response.status, response.read(_MAX_RESPONSE_BYTES))
    except urllib.error.HTTPError as exc:
        try:
            answer = exc.read(_MAX_RESPONSE_BYTES)
        except (OSError, http.client.HTTPException):
            answer = b""
        return classify_response(exc.code, answer)
    except urllib.error.URLError as exc:
        reason = exc.reason if isinstance(exc.reason, BaseException) else exc
        return Outcome("keep", type(reason).__name__)
    except ValueError:
        return Outcome("keep", "endpoint_misconfigured")
    except (OSError, http.client.HTTPException) as exc:
        return Outcome("keep", type(exc).__name__)


# --- Draining ---------------------------------------------------------------------


@dataclass
class DrainResult:
    """What one drain did."""

    sent: int = 0
    rejected: int = 0
    batches: int = 0
    error_class: str = ""
    busy: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "sent": self.sent,
            "rejected": self.rejected,
            "batches": self.batches,
            "error_class": self.error_class,
            "busy": self.busy,
        }


@contextlib.contextmanager
def _lock(directory: Path) -> Generator[bool]:
    """Hold the non-blocking drain lock; yield whether this process got it."""
    with (directory / ".drain.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _read_record(path: Path) -> Record | None:
    try:
        item = json.loads(path.read_text(encoding="utf-8"))
        family = FAMILIES[item["schema"]]
        _time_ns(item[family.time_field])
        str(item["event_id"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return item if isinstance(item, dict) else None


def _quarantine(paths: list[Path], directory: Path) -> None:
    quarantine = directory / "rejected"
    quarantine.mkdir(exist_ok=True)
    for path in paths:
        with contextlib.suppress(OSError):
            path.replace(quarantine / path.name)


def _ship(
    endpoint: str,
    paths: list[Path],
    directory: Path,
    state: dict[str, Any],
    result: DrainResult,
) -> bool:
    """Send one batch and settle its files; return False to stop draining."""
    batch: list[tuple[Path, Record]] = []
    invalid: list[Path] = []
    for path in paths:
        item = _read_record(path)
        if item is None:
            if path.exists():
                invalid.append(path)
        else:
            batch.append((path, item))
    if invalid:
        _quarantine(invalid, directory)
        result.rejected += len(invalid)
        hooks.log("spool", "invalid_record", str(len(invalid)))
    if not batch:
        return True
    outcome = send(endpoint, build_body([item for _, item in batch]))
    result.batches += 1
    files = [path for path, _ in batch]
    if outcome.action == "delete":
        sent_total = state["sent_total"]
        for _, item in batch:
            name = FAMILIES[item["schema"]].name
            sent_total[name] = sent_total.get(name, 0) + 1
        for path in files:
            path.unlink(missing_ok=True)
        result.sent += len(files)
        state["last_error_class"] = ""
        return True
    state["last_error_class"] = outcome.error_class
    hooks.log("spool", outcome.error_class, str(len(files)))
    if outcome.action == "reject":
        _quarantine(files, directory)
        result.rejected += len(files)
        return True
    result.error_class = outcome.error_class
    return False


def drain(
    endpoint: str,
    *,
    inbox: Path | None = None,
    budget: float = DRAIN_BUDGET_SECONDS,
    max_batches: int | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> DrainResult:
    """Send spooled records until a pass finds the spool empty, the budget ends, or a batch fails.

    One process drains at a time (a non-blocking lock); another holder makes this a
    no-op with ``busy`` set. Records written while draining are picked up by the next
    pass, so the loop ends only on an empty pass. ``max_batches`` bounds the work of a
    hook process, which must not outlast its harness's patience.
    """
    directory = _directory(inbox)
    result = DrainResult()
    if not directory.is_dir():
        return result
    deadline = clock() + budget
    with _lock(directory) as held:
        if not held:
            result.busy = True
            return result
        state = read_state(directory)
        while clock() < deadline and (max_batches is None or result.batches < max_batches):
            paths = pending_paths(directory, BATCH_SIZE)
            if not paths:
                break
            keep_going = _ship(endpoint, paths, directory, state, result)
            _save_state(directory, state)
            if not keep_going or all(path.exists() for path in paths):
                break
    return result


def hook_endpoint(
    config: Path | None = None, environ: Mapping[str, str] | None = None
) -> str | None:
    """Return the logs endpoint for the hook path, loading configuration leniently.

    A missing configuration file is fine (the environment fallback applies); an invalid
    one means no endpoint, so records stay spooled, and its error class is logged.
    """
    try:
        configured = load_config(config).hooks_otlp_logs_endpoint
    except Exception as exc:
        hooks.log("spool", type(exc).__name__, "0")
        return None
    return resolve_otlp_logs_endpoint(configured, environ)


# --- Status and sync --------------------------------------------------------------


def status_record(inbox: Path | None = None, *, now: datetime | None = None) -> Record:
    """Build the spool-status heartbeat: backlog, quarantine, age, send counters, last error."""
    directory = _directory(inbox)
    moment = now if now is not None else datetime.now(UTC)
    oldest: float | None = None
    pending = 0
    for path in pending_paths(directory):
        pending += 1
        with contextlib.suppress(OSError):
            modified = path.stat().st_mtime
            oldest = modified if oldest is None else min(oldest, modified)
    state = read_state(directory)
    reported_at = hooks.timestamp(moment)
    host = host_name()
    return {
        "schema": STATUS_SCHEMA,
        "event_id": hashlib.sha256(f"{STATUS_SCHEMA}\0{host}\0{reported_at}".encode()).hexdigest(),
        "reported_at": reported_at,
        "pending": pending,
        "rejected": rejected_count(directory),
        "oldest_pending_seconds": (
            None if oldest is None else max(0, int(moment.timestamp() - oldest))
        ),
        "sent_total": {name: state["sent_total"].get(name, 0) for name in _COUNTED},
        "last_error_class": state["last_error_class"],
        "cli_version": cli_version(),
    }


def sync(
    config: AppConfig,
    *,
    rescan: bool = False,
    inbox: Path | None = None,
    scan_state: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Attribute new sessions into the spool, record a status heartbeat, and drain.

    Without an endpoint nothing is sent (and no heartbeat is written, which would only
    pile up), so records wait in the spool until one is configured.
    """
    from agent_introspection import sessions

    directory = _directory(inbox)
    attributed = sessions.resolve(
        stores=sessions.load_stores(config.session_roots),
        state=scan_state,
        rescan=rescan,
        inbox=directory,
    )
    endpoint = resolve_otlp_logs_endpoint(config.hooks_otlp_logs_endpoint, environ)
    if endpoint is None:
        return {"sessions": attributed, "drain": {"skipped": "no_endpoint"}}
    status = status_record(directory)
    hooks.write(status, directory)
    sent = drain(endpoint, inbox=directory)
    return {
        "sessions": attributed,
        "status": {key: status[key] for key in ("pending", "rejected")},
        "drain": sent.as_dict(),
    }

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
from agent_introspection.config import AppConfig, ConfigurationError, load_config

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
    name: str
    event_name: str
    time_field: str


FAMILIES: dict[str, Family] = {
    hooks.SCHEMA: Family("hook-event", "agent_introspection.hook_event", "occurred_at"),
    STATUS_SCHEMA: Family("spool-status", "agent_introspection.spool_status", "reported_at"),
}
_COUNTED = ("hook-event",)
RETIRED_SCHEMAS = frozenset({"agent-introspection.session-project/1"})


def host_name() -> str:
    return socket.gethostname().split(".")[0]


def cli_version() -> str:
    try:
        return metadata.version("agent-introspection")
    except metadata.PackageNotFoundError:
        return "unknown"


def _urlopen(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)


def _directory(inbox: Path | None) -> Path:
    return inbox if inbox is not None else hooks.inbox_dir()


def _state_path(inbox: Path) -> Path:
    return inbox.parent / "spool-state.json"


def pending_paths(inbox: Path | None = None, limit: int | None = None) -> list[Path]:
    directory = _directory(inbox)
    return list(islice(directory.glob("*.json"), limit)) if directory.is_dir() else []


def backlog(inbox: Path | None = None) -> int:
    return len(pending_paths(inbox))


def rejected_count(inbox: Path | None = None) -> int:
    quarantine = _directory(inbox) / "rejected"
    return len(list(quarantine.glob("*.json"))) if quarantine.is_dir() else 0


def read_state(inbox: Path | None = None) -> dict[str, Any]:
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


def _time_ns(value: str) -> int:
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return (moment - _EPOCH) // timedelta(microseconds=1) * 1000


def _attribute(key: str, value: str) -> dict[str, Any]:
    return {"key": key, "value": {"stringValue": value}}


def build_body(records: list[Record]) -> bytes:
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


@dataclass(frozen=True)
class Outcome:
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
    text = body.strip()
    if 200 <= status < 300:
        return _classify_success(text)
    if status == 400 and _is_otlp_status(text):
        return Outcome("reject", "rejected_batch")
    if status == 429 or status >= 500:
        return Outcome("keep", f"http_{status}")
    return Outcome("keep", "endpoint_misconfigured")


def send(endpoint: str, body: bytes, timeout: float = CONNECT_TIMEOUT_SECONDS) -> Outcome:
    request = urllib.request.Request(
        endpoint, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with _urlopen(request, timeout) as response:
            return classify_response(response.status, response.read(_MAX_RESPONSE_BYTES))
    except urllib.error.HTTPError as exc:
        with exc:
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


@dataclass
class DrainResult:
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


def _left_to_quarantine(path: Path) -> bool:
    try:
        item = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return path.exists()
    if isinstance(item, dict) and item.get("schema") in RETIRED_SCHEMAS:
        path.unlink(missing_ok=True)
        return False
    return True


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
    batch: list[tuple[Path, Record]] = []
    invalid: list[Path] = []
    for path in paths:
        item = _read_record(path)
        if item is None:
            if _left_to_quarantine(path):
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
    directory = _directory(inbox)
    result = DrainResult()
    if not directory.is_dir():
        return result
    with _lock(directory) as held:
        if not held:
            result.busy = True
            return result
        deadline = clock() + budget
        _drain_held(endpoint, directory, result, lambda: clock() < deadline, max_batches)
    return result


def _drain_held(
    endpoint: str,
    directory: Path,
    result: DrainResult,
    in_budget: Callable[[], bool],
    max_batches: int | None,
) -> None:
    state = read_state(directory)
    while in_budget() and (max_batches is None or result.batches < max_batches):
        paths = pending_paths(directory, BATCH_SIZE)
        if not paths:
            break
        keep_going = _ship(endpoint, paths, directory, state, result)
        _save_state(directory, state)
        if not keep_going or all(path.exists() for path in paths):
            break


def _prune_heartbeats(directory: Path) -> None:
    for path in pending_paths(directory):
        item = _read_record(path)
        if item is not None and item["schema"] == STATUS_SCHEMA:
            path.unlink(missing_ok=True)


def hook_endpoint(config: Path | None = None) -> str | None:
    try:
        return load_config(config).otlp_logs_endpoint
    except ConfigurationError as exc:
        hooks.log("spool", type(exc).__name__, "0")
        return None


def status_record(inbox: Path | None = None, *, now: datetime | None = None) -> Record:
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
    inbox: Path | None = None,
) -> dict[str, Any]:
    directory = _directory(inbox)
    endpoint = config.otlp_logs_endpoint
    sent = DrainResult()
    directory.mkdir(parents=True, exist_ok=True)
    with _lock(directory) as held:
        if not held:
            sent.busy = True
            return {"drain": sent.as_dict()}
        _prune_heartbeats(directory)
        deadline = time.monotonic() + DRAIN_BUDGET_SECONDS
        _drain_held(endpoint, directory, sent, lambda: time.monotonic() < deadline, None)
        status = status_record(directory)
        beat = hooks.write(status, directory)
        state = read_state(directory)
        _ship(endpoint, [beat], directory, state, sent)
        _save_state(directory, state)
    return {
        "status": {key: status[key] for key in ("pending", "rejected")},
        "drain": sent.as_dict(),
    }

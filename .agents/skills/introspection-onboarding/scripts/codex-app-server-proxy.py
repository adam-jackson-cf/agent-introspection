#!/usr/bin/env python3
"""Proxy Codex app-server JSONL while forwarding only lifecycle identity metadata."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import queue
import re
import signal
import subprocess
import sys
import tempfile
import threading
from collections import OrderedDict
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import FrameType
from typing import Any, BinaryIO, Final, cast

_REAL_CLI_CONFIG: Final = "codex-app-server-real-cli"
_ADAPTER: Final = "adapters/codex-app-server.sh"
_RELEVANT_METHODS: Final = frozenset(
    {
        "thread/start",
        "thread/resume",
        "thread/delete",
        "thread/started",
        "thread/settings/updated",
        "thread/deleted",
    }
)
_NUMBER = re.compile(rb"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?")
_MAX_DEPTH: Final = 64
_MAX_PENDING: Final = 1024
_EVENT_QUEUE_SIZE: Final = 256
_MAX_STATE: Final = 4096


class MetadataError(ValueError):
    """A protocol line cannot provide unambiguous approved metadata."""


RequestKey = tuple[str, str]


@dataclass(frozen=True)
class SelectedMessage:
    request_id: RequestKey | None = None
    method: str | None = None
    params_thread_id: str | None = None
    params_cwd: str | None = None
    params_thread_id_from_thread: str | None = None
    params_thread_cwd: str | None = None
    params_settings_cwd: str | None = None
    result_present: bool = False
    error_present: bool = False
    result_thread_id: str | None = None
    result_cwd: str | None = None
    result_thread_cwd: str | None = None


@dataclass(frozen=True)
class PendingRequest:
    method: str
    thread_id: str | None


@dataclass(frozen=True)
class LifecycleEvent:
    kind: str
    thread_id: str
    workspace: str | None
    occurred_at: str


class SelectiveJsonScanner:
    """Decode approved scalar paths and structurally skip every other JSON value."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.index = 0

    def scan(self) -> SelectedMessage:
        values: dict[str, object] = {}
        self._object(self._top_field, values, 0)
        self._space()
        if self.index != len(self.data):
            raise MetadataError
        return SelectedMessage(
            request_id=cast(RequestKey | None, values.get("request_id")),
            method=cast(str | None, values.get("method")),
            params_thread_id=cast(str | None, values.get("params_thread_id")),
            params_cwd=cast(str | None, values.get("params_cwd")),
            params_thread_id_from_thread=cast(
                str | None, values.get("params_thread_id_from_thread")
            ),
            params_thread_cwd=cast(str | None, values.get("params_thread_cwd")),
            params_settings_cwd=cast(str | None, values.get("params_settings_cwd")),
            result_present=values.get("result_present") is True,
            error_present=values.get("error_present") is True,
            result_thread_id=cast(str | None, values.get("result_thread_id")),
            result_cwd=cast(str | None, values.get("result_cwd")),
            result_thread_cwd=cast(str | None, values.get("result_thread_cwd")),
        )

    def _top_field(self, key: str, values: dict[str, object], depth: int) -> None:
        if key == "id":
            self._unique(values, "request_id", self._request_id())
        elif key == "method":
            self._unique(values, "method", self._required_string())
        elif key == "params":
            nested: dict[str, object] = {}
            self._object(self._params_field, nested, depth)
            values.update(nested)
        elif key == "result":
            if values.get("result_present") is True:
                raise MetadataError
            values["result_present"] = True
            nested = {}
            self._object(self._result_field, nested, depth)
            values.update(nested)
        elif key == "error":
            if values.get("error_present") is True:
                raise MetadataError
            values["error_present"] = True
            self._skip(depth)
        else:
            self._skip(depth)

    def _params_field(self, key: str, values: dict[str, object], depth: int) -> None:
        if key == "threadId":
            self._unique(values, "params_thread_id", self._required_string())
        elif key == "cwd":
            self._unique(values, "params_cwd", self._required_string())
        elif key == "thread":
            nested: dict[str, object] = {}
            self._object(self._thread_field, nested, depth)
            if "thread_id" in nested:
                self._unique(values, "params_thread_id_from_thread", nested["thread_id"])
            if "thread_cwd" in nested:
                self._unique(values, "params_thread_cwd", nested["thread_cwd"])
        elif key == "threadSettings":
            nested = {}
            self._object(self._settings_field, nested, depth)
            if "settings_cwd" in nested:
                self._unique(values, "params_settings_cwd", nested["settings_cwd"])
        else:
            self._skip(depth)

    def _result_field(self, key: str, values: dict[str, object], depth: int) -> None:
        if key == "cwd":
            self._unique(values, "result_cwd", self._required_string())
        elif key == "thread":
            nested: dict[str, object] = {}
            self._object(self._thread_field, nested, depth)
            if "thread_id" in nested:
                self._unique(values, "result_thread_id", nested["thread_id"])
            if "thread_cwd" in nested:
                self._unique(values, "result_thread_cwd", nested["thread_cwd"])
        else:
            self._skip(depth)

    def _thread_field(self, key: str, values: dict[str, object], depth: int) -> None:
        if key == "id":
            self._unique(values, "thread_id", self._required_string())
        elif key == "cwd":
            self._unique(values, "thread_cwd", self._required_string())
        else:
            self._skip(depth)

    def _settings_field(self, key: str, values: dict[str, object], depth: int) -> None:
        if key == "cwd":
            self._unique(values, "settings_cwd", self._required_string())
        else:
            self._skip(depth)

    def _object(
        self,
        field: Callable[[str, dict[str, object], int], None],
        values: dict[str, object],
        depth: int,
    ) -> None:
        if depth >= _MAX_DEPTH:
            raise MetadataError
        self._space()
        self._expect(ord("{"))
        self._space()
        seen: set[str] = set()
        if self._take(ord("}")):
            return
        while True:
            key = self._required_string()
            if key in seen and key in {
                "id",
                "method",
                "params",
                "result",
                "error",
                "threadId",
                "cwd",
                "thread",
                "threadSettings",
            }:
                raise MetadataError
            seen.add(key)
            self._space()
            self._expect(ord(":"))
            self._space()
            field(key, values, depth + 1)
            self._space()
            if self._take(ord("}")):
                return
            self._expect(ord(","))
            self._space()

    def _skip(self, depth: int) -> None:
        if depth >= _MAX_DEPTH:
            raise MetadataError
        self._space()
        if self.index >= len(self.data):
            raise MetadataError
        current = self.data[self.index]
        if current == ord('"'):
            self._skip_string()
            return
        if current == ord("{"):
            self._skip_object(depth + 1)
            return
        if current == ord("["):
            self._skip_array(depth + 1)
            return
        for literal in (b"true", b"false", b"null"):
            if self.data.startswith(literal, self.index):
                self.index += len(literal)
                return
        match = _NUMBER.match(self.data, self.index)
        if match is None:
            raise MetadataError
        self.index = match.end()

    def _skip_object(self, depth: int) -> None:
        self._expect(ord("{"))
        self._space()
        if self._take(ord("}")):
            return
        while True:
            self._skip_string()
            self._space()
            self._expect(ord(":"))
            self._skip(depth)
            self._space()
            if self._take(ord("}")):
                return
            self._expect(ord(","))
            self._space()

    def _skip_array(self, depth: int) -> None:
        self._expect(ord("["))
        self._space()
        if self._take(ord("]")):
            return
        while True:
            self._skip(depth)
            self._space()
            if self._take(ord("]")):
                return
            self._expect(ord(","))
            self._space()

    def _request_id(self) -> RequestKey:
        self._space()
        if self.index < len(self.data) and self.data[self.index] == ord('"'):
            return ("string", self._required_string())
        match = _NUMBER.match(self.data, self.index)
        if match is None:
            raise MetadataError
        token = match.group(0)
        self.index = match.end()
        try:
            value = Decimal(token.decode("ascii"))
        except (InvalidOperation, UnicodeDecodeError) as error:
            raise MetadataError from error
        return ("number", str(value.normalize()))

    def _required_string(self) -> str:
        start, end = self._string_bounds()
        try:
            value = json.loads(self.data[start:end])
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise MetadataError from error
        if not isinstance(value, str):
            raise MetadataError
        return value

    def _skip_string(self) -> None:
        self._string_bounds()

    def _string_bounds(self) -> tuple[int, int]:
        self._space()
        start = self.index
        self._expect(ord('"'))
        escaped = False
        while self.index < len(self.data):
            current = self.data[self.index]
            self.index += 1
            if escaped:
                escaped = False
                continue
            if current == ord("\\"):
                escaped = True
            elif current == ord('"'):
                return start, self.index
            elif current < 0x20:
                raise MetadataError
        raise MetadataError

    def _space(self) -> None:
        while self.index < len(self.data) and self.data[self.index] in b" \t\r\n":
            self.index += 1

    def _expect(self, token: int) -> None:
        if self.index >= len(self.data) or self.data[self.index] != token:
            raise MetadataError
        self.index += 1

    def _take(self, token: int) -> bool:
        if self.index < len(self.data) and self.data[self.index] == token:
            self.index += 1
            return True
        return False

    @staticmethod
    def _unique(values: dict[str, object], key: str, value: object) -> None:
        if key in values:
            raise MetadataError
        values[key] = value


def _scan_line(line: bytes) -> SelectedMessage:
    return SelectiveJsonScanner(line).scan()


def _valid_identity(value: str | None) -> bool:
    return bool(value) and not any(ord(character) < 0x20 for character in value)


def _valid_workspace(value: str | None) -> bool:
    return (
        _valid_identity(value)
        and value is not None
        and os.path.isabs(value)
        and os.path.isdir(value)
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _diagnostic(message: str) -> None:
    print(f"codex-app-server-proxy: {message}", file=sys.stderr, flush=True)


class LifecycleWorker:
    def __init__(self, script_dir: Path) -> None:
        self.adapter = script_dir / _ADAPTER
        state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
        self.state_dir = state_home / "agent-introspection/codex-app-server-threads"
        self.events: queue.Queue[LifecycleEvent | None] = queue.Queue(_EVENT_QUEUE_SIZE)
        self.thread = threading.Thread(target=self._run, name="codex-app-server-context")

    def start(self) -> None:
        self.thread.start()

    def submit(self, event: LifecycleEvent) -> None:
        try:
            self.events.put_nowait(event)
        except queue.Full:
            _diagnostic("lifecycle event queue capacity was exceeded")

    def close(self) -> None:
        self.events.put(None)
        self.thread.join()

    def _run(self) -> None:
        while True:
            event = self.events.get()
            if event is None:
                return
            try:
                self._apply(event)
            except MetadataError:
                pass
            except OSError:
                _diagnostic("lifecycle metadata could not be persisted")

    def _apply(self, event: LifecycleEvent) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.state_dir, 0o700)
        digest = hashlib.sha256(event.thread_id.encode()).hexdigest()
        state_path = self.state_dir / f"{digest}.json"
        lock_path = self.state_dir / ".state.lock"
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            with os.fdopen(descriptor, "r+") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                state = self._read_state(state_path, event.thread_id)
                if event.kind == "start":
                    if state is not None:
                        if state == event.workspace:
                            return
                        _diagnostic("conflicting repeated thread start was rejected")
                        return
                    if event.workspace is None:
                        return
                    state_count = sum(path.suffix == ".json" for path in self.state_dir.iterdir())
                    if state_count >= _MAX_STATE:
                        _diagnostic("managed thread state capacity was exceeded")
                        return
                    if not self._invoke(event, "session_start"):
                        return
                    self._write_state(state_path, event.thread_id, event.workspace)
                    return
                if state is None:
                    _diagnostic("lifecycle metadata without an observed thread start was rejected")
                    return
                if event.kind == "observe":
                    if event.workspace is None or state == event.workspace:
                        return
                    if self._invoke(event, "workspace_changed"):
                        self._write_state(state_path, event.thread_id, event.workspace)
                    return
                if event.kind == "end" and self._invoke(
                    LifecycleEvent(event.kind, event.thread_id, state, event.occurred_at),
                    "session_end",
                ):
                    state_path.unlink(missing_ok=True)
        finally:
            if not getattr(descriptor, "closed", False):
                with suppress(OSError):
                    os.close(descriptor)

    def _invoke(self, event: LifecycleEvent, event_type: str) -> bool:
        workspace = event.workspace
        if not _valid_identity(event.thread_id) or not _valid_workspace(workspace):
            _diagnostic("invalid lifecycle identity metadata was rejected")
            return False
        assert workspace is not None
        completed = subprocess.run(
            [
                str(self.adapter),
                event.thread_id,
                workspace,
                event_type,
                event.occurred_at,
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if completed.returncode != 0:
            _diagnostic("session-context adapter rejected lifecycle metadata")
            return False
        return True

    @staticmethod
    def _read_state(path: Path, thread_id: str) -> str | None:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as error:
            _diagnostic("invalid managed thread state was rejected")
            raise MetadataError from error
        if (
            not isinstance(payload, dict)
            or set(payload) != {"schema_version", "thread_id", "workspace"}
            or payload.get("schema_version") != 1
            or payload.get("thread_id") != thread_id
            or not _valid_workspace(payload.get("workspace"))
        ):
            _diagnostic("invalid managed thread state was rejected")
            raise MetadataError
        return str(payload["workspace"])

    def _write_state(self, path: Path, thread_id: str, workspace: str) -> None:
        payload = {
            "schema_version": 1,
            "thread_id": thread_id,
            "workspace": workspace,
        }
        descriptor, temporary = tempfile.mkstemp(prefix=".thread-", dir=self.state_dir)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(payload, output, sort_keys=True, separators=(",", ":"))
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            with suppress(FileNotFoundError):
                os.unlink(temporary)


class ProtocolObserver:
    def __init__(self, worker: LifecycleWorker) -> None:
        self.worker = worker
        self.pending: OrderedDict[RequestKey, PendingRequest] = OrderedDict()
        self.completed_deletes: OrderedDict[str, None] = OrderedDict()
        self.lock = threading.Lock()

    def client_line(self, line: bytes) -> None:
        try:
            message = _scan_line(line)
        except MetadataError:
            if any(method.encode() in line for method in _RELEVANT_METHODS):
                _diagnostic("malformed lifecycle request was rejected")
            return
        if message.method not in {"thread/start", "thread/resume", "thread/delete"}:
            return
        if message.request_id is None:
            _diagnostic("lifecycle request without a valid request id was rejected")
            return
        thread_id = message.params_thread_id
        if message.method != "thread/start" and not _valid_identity(thread_id):
            _diagnostic("lifecycle request without a valid thread id was rejected")
            return
        with self.lock:
            if message.request_id in self.pending:
                self.pending.pop(message.request_id)
                _diagnostic("duplicate lifecycle request id was rejected")
                return
            if len(self.pending) >= _MAX_PENDING:
                self.pending.popitem(last=False)
                _diagnostic("oldest pending lifecycle request was rejected")
            self.pending[message.request_id] = PendingRequest(message.method, thread_id)

    def server_line(self, line: bytes) -> None:
        try:
            message = _scan_line(line)
        except MetadataError:
            if any(method.encode() in line for method in _RELEVANT_METHODS):
                _diagnostic("malformed lifecycle response was rejected")
            return
        if message.method in {
            "thread/started",
            "thread/settings/updated",
            "thread/deleted",
        }:
            self._notification(message)
            return
        if message.request_id is None:
            return
        with self.lock:
            pending = self.pending.pop(message.request_id, None)
        if pending is None or message.error_present or not message.result_present:
            return
        self._response(pending, message)

    def _response(self, pending: PendingRequest, message: SelectedMessage) -> None:
        if pending.method == "thread/delete":
            assert pending.thread_id is not None
            self._submit_end(pending.thread_id)
            return
        thread_id = message.result_thread_id
        workspace = message.result_cwd
        if (
            not _valid_identity(thread_id)
            or not _valid_workspace(workspace)
            or message.result_thread_cwd != workspace
        ):
            _diagnostic("lifecycle response without exact thread identity metadata was rejected")
            return
        if pending.method == "thread/resume" and thread_id != pending.thread_id:
            _diagnostic("resume response thread identity mismatch was rejected")
            return
        kind = "start" if pending.method == "thread/start" else "observe"
        assert thread_id is not None and workspace is not None
        self.worker.submit(LifecycleEvent(kind, thread_id, workspace, _now()))

    def _notification(self, message: SelectedMessage) -> None:
        if message.method == "thread/started":
            thread_id = message.params_thread_id_from_thread
            workspace = message.params_thread_cwd
            kind = "start"
        elif message.method == "thread/settings/updated":
            thread_id = message.params_thread_id
            workspace = message.params_settings_cwd
            kind = "observe"
        else:
            thread_id = message.params_thread_id
            workspace = None
            kind = "end"
        if not _valid_identity(thread_id) or (kind != "end" and not _valid_workspace(workspace)):
            _diagnostic("lifecycle notification without exact identity metadata was rejected")
            return
        assert thread_id is not None
        if kind == "end":
            self._submit_end(thread_id)
            return
        self.worker.submit(LifecycleEvent(kind, thread_id, workspace, _now()))

    def _submit_end(self, thread_id: str) -> None:
        with self.lock:
            if thread_id in self.completed_deletes:
                return
            if len(self.completed_deletes) >= _MAX_PENDING:
                self.completed_deletes.popitem(last=False)
            self.completed_deletes[thread_id] = None
        self.worker.submit(LifecycleEvent("end", thread_id, None, _now()))


def _real_cli(script_dir: Path) -> Path:
    config = script_dir / _REAL_CLI_CONFIG
    try:
        lines = config.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise MetadataError from error
    if len(lines) != 1:
        raise MetadataError
    candidate = Path(lines[0])
    if not candidate.is_absolute() or not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise MetadataError
    if candidate.resolve() == Path(__file__).resolve():
        raise MetadataError
    return candidate


def _child_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("CODEX_CLI_PATH", None)
    return environment


def _pump_client(source: BinaryIO, destination: BinaryIO, observer: ProtocolObserver) -> None:
    try:
        for line in source:
            destination.write(line)
            destination.flush()
            observer.client_line(line)
    except (BrokenPipeError, OSError):
        pass
    finally:
        with suppress(OSError):
            destination.close()


def _proxy(real_cli: Path, argv: list[str], script_dir: Path) -> int:
    child = subprocess.Popen(
        [str(real_cli), *argv[1:]],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=None,
        env=_child_environment(),
    )
    assert child.stdin is not None and child.stdout is not None
    worker = LifecycleWorker(script_dir)
    observer = ProtocolObserver(worker)
    worker.start()
    client = threading.Thread(
        target=_pump_client,
        args=(sys.stdin.buffer, child.stdin, observer),
        name="codex-app-server-client",
        daemon=True,
    )
    client.start()

    def forward_signal(signum: int, _frame: FrameType | None) -> None:
        with suppress(ProcessLookupError):
            child.send_signal(signum)

    previous: dict[
        int,
        int | signal.Handlers | Callable[[int, FrameType | None], object] | None,
    ] = {}
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        previous[signum] = signal.signal(signum, forward_signal)
    try:
        try:
            for line in child.stdout:
                sys.stdout.buffer.write(line)
                sys.stdout.buffer.flush()
                observer.server_line(line)
        except BrokenPipeError:
            child.terminate()
        return child.wait()
    finally:
        worker.close()
        for signum, handler in previous.items():
            signal.signal(signum, cast(Any, handler))


def _exit_like_child(returncode: int) -> int:
    if returncode >= 0:
        return returncode
    signum = -returncode
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)
    return 128 + signum


def main(argv: list[str]) -> int:
    script_dir = Path(__file__).resolve().parent
    try:
        real_cli = _real_cli(script_dir)
    except MetadataError:
        _diagnostic("configured real Codex executable is unavailable")
        return os.EX_UNAVAILABLE
    if argv[1:2] != ["app-server"]:
        os.execve(str(real_cli), [str(real_cli), *argv[1:]], _child_environment())
    return _exit_like_child(_proxy(real_cli, argv, script_dir))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

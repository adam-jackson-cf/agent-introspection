"""Activity hooks: normalize one harness hook envelope into hook-event records.

The activity shim hands each hook envelope (JSON on stdin) to
``agent-introspection hook <producer> <event>``, which runs ``main`` detached from
the harness. ``normalize`` reduces the envelope to the fields in
``docs/hook-events.md`` and ``write`` stores each record atomically in the inbox
that ``facts sync`` drains into ``introspection.hook_events``.

Raw prompt, argument, and output text is read only in memory. The derivations
mirror the Codex projection in ``facts_sql/select_logs.sql`` so a field means the
same thing for every harness. Prompts are only measured here (ID, length, whether a
turn was running); their text reaches the Jev classifier through the producers' own
OTLP prompt events, which ``facts sync`` labels (``classify.py``).

Turn state: Claude Code and omp have no native "a turn is running" flag on the
prompt hook, so a small per-session marker file under ``STATE_DIR`` is created
when a prompt is submitted and removed when the turn stops (Claude ``Stop`` /
``StopFailure``). A marker older than
``TURN_STALE_SECONDS`` counts as closed (the harness may have exited mid-turn).
The marker holds only the prompt ID and a timestamp.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = "agent-introspection.hook-event/1"
DATA_DIR = Path.home() / ".local/share/agent-introspection"
INBOX = DATA_DIR / "hook-inbox"
STATE_DIR = DATA_DIR / "hook-state"
LOG = DATA_DIR / "hooks.log"
TURN_STALE_SECONDS = 6 * 3600
MAX_TARGETS = 20
TARGET_CHARS = 300
SIGNATURE_CHARS = 160
PRODUCERS = ("claude-code", "omp")

type Record = dict[str, Any]
type Attrs = dict[str, Any]
type Envelope = dict[str, Any]

# --- Derivations mirrored from facts_sql/select_logs.sql -------------------------

_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SUBCOMMAND = re.compile(r"^[a-z][a-z0-9:_.-]*$")
_PATH_TOKEN = re.compile(r"^[~.]{0,2}/?[A-Za-z0-9_.@+-]+(/[A-Za-z0-9_.@+-]+)*/?$")
_FILE_EXTENSION = re.compile(
    r"\.(md|py|ts|tsx|js|mjs|json|jsonl|toml|ya?ml|sh|sql|txt|lock|css|html|rs|go|sqlite3?"
    r"|cfg|ini|csv|xml|plist|log)$"
)
_NUMBER = re.compile(r"^[0-9.]+$")
_TOKEN_QUOTES = (re.compile(r"^[\"']+"), re.compile(r"[\"';,)]+$"))
_PATCH_FILE = re.compile(r"\*\*\* (?:Update|Add|Delete) File: ([^\n\\]+)")
_HOME_ANYWHERE = re.compile(r"/Users/[^/ ]+")
_GATE_BYPASS = re.compile(r"(--no-verify|(^|[\s\"'])HUSKY=0|(^|[\s\"'])SKIP=|--no-gpg-sign)")
_OUTPUT_NOISE = re.compile(
    r"^(Chunk ID|Wall time|Process exited|Exit code|Original token count|Output:"
    r"|Script completed|Script running)"
)
_DIAGNOSTIC = re.compile(
    r"(?i)(error|fail|denied|not permitted|no such|traceback|exception|cannot|can't|invalid"
    r"|not found)"
)
_DIAGNOSTIC_TAIL = re.compile(
    r"(?i)((error|fail|denied|not permitted|no such|traceback|exception|cannot|can't|invalid"
    r"|not found).*)"
)
_EXCEPTION_LINE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_.]*(Error|Exception|Exit|Interrupt)(\s*\[[A-Z_]+\])?(:|$)"
)
_GENERIC_LINE = re.compile(
    r"^\s*(Traceback \(most recent call last\):|triggerUncaughtException\(|at |File \""
    r"|node:internal|throw |raise |Script (failed|error:?)\s*$"
    r"|error: script \"[^\"]*\" exited|={3,}|- Status: failed)"
)
_DIGITS = re.compile(r"[0-9a-f]{8,}|\d+")
_PATH_KEYS = ("path", "file_path", "notebook_path")
_ERROR_CLASS = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


_HOME_PATHS = re.compile(r"/Users/[^/\s;:,'\"]+")


def redact_home(path: str) -> str:
    """Replace every ``/Users/<name>`` with ``~``; one argument can hold several paths."""
    return _HOME_PATHS.sub("~", path)


def canonical_arguments(arguments: object) -> str:
    """Return the canonical JSON text of tool arguments (sorted keys, compact)."""
    return json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def command_text(arguments: object) -> str:
    """Return the shell command a tool call runs (``command`` or ``cmd``), else ``''``."""
    if not isinstance(arguments, dict):
        return ""
    for key in ("command", "cmd"):
        value = arguments.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list) and all(isinstance(part, str) for part in value):
            return " ".join(value)
    return ""


def command_tokens(command: str) -> list[str]:
    """Split on whitespace, dropping empty tokens and environment assignments."""
    return [
        token for token in re.split(r"\s+", command) if token and not _ENV_ASSIGNMENT.match(token)
    ]


def command_head(tokens: list[str]) -> str:
    """Return the basename of the first command token."""
    return tokens[0].split("/")[-1] if tokens else ""


# Only tools whose second token is a subcommand keep it, so a free-text argument
# (`echo word`) never becomes a stored subcommand. Mirrors select_logs.sql.
SUBCOMMAND_HEADS = frozenset((
    "aws",
    "brew",
    "bun",
    "bunx",
    "cargo",
    "claude",
    "clawpatch",
    "codegraph",
    "codex",
    "defaults",
    "deno",
    "docker",
    "gcloud",
    "gh",
    "git",
    "go",
    "hdiutil",
    "helm",
    "infisical",
    "just",
    "kubectl",
    "launchctl",
    "make",
    "npm",
    "npx",
    "omp",
    "orca",
    "pip",
    "pipx",
    "pnpm",
    "poetry",
    "security",
    "systemctl",
    "terraform",
    "uv",
    "volta",
    "yarn",
))


def command_sub(tokens: list[str]) -> str:
    """Return the second token of a known subcommand-style tool when it looks like one."""
    if len(tokens) < 2 or command_head(tokens) not in SUBCOMMAND_HEADS:
        return ""
    return tokens[1] if _SUBCOMMAND.match(tokens[1]) else ""


def _strip_token(token: str) -> str:
    for pattern in _TOKEN_QUOTES:
        token = pattern.sub("", token)
    return token


def _is_path_token(token: str) -> bool:
    return (
        not token.startswith("-")
        and _PATH_TOKEN.match(token) is not None
        and ("/" in token or _FILE_EXTENSION.search(token) is not None)
        and _NUMBER.match(token) is None
    )


def command_paths(tokens: list[str]) -> list[str]:
    """Return path-like command arguments (a slash or a known file extension)."""
    return [token for token in map(_strip_token, tokens[1:]) if _is_path_token(token)]


def targets(arguments: object, canonical: str, tokens: list[str]) -> list[str]:
    """Return distinct home-redacted target paths, at most ``MAX_TARGETS``."""
    found = _PATCH_FILE.findall(canonical)
    if isinstance(arguments, dict):
        found += [
            value for key in _PATH_KEYS if isinstance(value := arguments.get(key), str) and value
        ]
    found += command_paths(tokens)
    distinct = dict.fromkeys(redact_home(path.strip())[:TARGET_CHARS] for path in found)
    return list(distinct)[:MAX_TARGETS]


def gate_bypass(canonical: str) -> int:
    """Return 1 when the arguments skip a commit gate (``--no-verify``, ``HUSKY=0``, ...)."""
    return int(_GATE_BYPASS.search(canonical) is not None)


def arguments_hash(canonical: str) -> str:
    """Return the first 16 hex digits of SHA-256 over the canonical arguments."""
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _error_line(output: str) -> str:
    lines = [line for line in output.split("\n") if line and not _OUTPUT_NOISE.match(line)]
    diagnostic = [line for line in lines if _DIAGNOSTIC.search(line)]
    exception = [line for line in lines if _EXCEPTION_LINE.match(line)]
    if exception:
        return exception[-1]
    specific = [line for line in diagnostic if not _GENERIC_LINE.match(line)]
    if specific:
        return specific[0]
    return diagnostic[0] if diagnostic else ""


def failure_signature(output: str) -> str:
    """Return the most specific diagnostic line, home as ``~``, digits as ``N``, capped."""
    line = _error_line(output)
    if not _DIAGNOSTIC.search(line[:SIGNATURE_CHARS]):
        tail = _DIAGNOSTIC_TAIL.search(line)
        line = tail.group(1) if tail else ""
    return _DIGITS.sub("N", _HOME_ANYWHERE.sub("~", line))[:SIGNATURE_CHARS]


# --- Records ----------------------------------------------------------------------


type Clock = Callable[[], datetime]


@dataclass
class Context:
    """Where turn state lives, and the clock records are stamped with."""

    state_dir: Path = field(default_factory=lambda: STATE_DIR)
    now: Clock = field(default=lambda: datetime.now(UTC))


def timestamp(moment: datetime) -> str:
    """Return an RFC 3339 UTC timestamp with microseconds."""
    return moment.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def record(head: tuple[str, str, str], occurred_at: str, key: str, attrs: Attrs) -> Record:
    """Build one record; ``head`` is (producer, session_id, event_type)."""
    producer, session_id, event_type = head
    digest = hashlib.sha256(
        "\0".join((producer, session_id, event_type, occurred_at, key)).encode()
    ).hexdigest()
    return {
        "schema": SCHEMA,
        "event_id": digest,
        "producer": producer,
        "session_id": session_id,
        "event_type": event_type,
        "occurred_at": occurred_at,
        "attrs": attrs,
    }


def _text(envelope: Envelope, key: str) -> str:
    value = envelope.get(key)
    return value if isinstance(value, str) else ""


def _session(envelope: Envelope) -> str:
    session_id = _text(envelope, "session_id")
    if not session_id or any(ord(char) < 32 or ord(char) == 127 for char in session_id):
        raise ValueError("session_id is required")
    return session_id


def tool_call_attrs(envelope: Envelope) -> Attrs:
    """Derive ``tool_call`` attributes from a tool name, input, and working directory."""
    arguments = envelope.get("tool_input", {})
    canonical = canonical_arguments(arguments)
    tokens = command_tokens(command_text(arguments))
    workdir = ""
    if isinstance(arguments, dict):
        workdir = next(
            (v for k in ("workdir", "cwd") if isinstance(v := arguments.get(k), str) and v), ""
        )
    return {
        "tool_use_id": _text(envelope, "tool_use_id"),
        "tool_name": _text(envelope, "tool_name"),
        "arguments_hash": arguments_hash(canonical),
        "arguments_length": len(canonical.encode()),
        "command_head": command_head(tokens),
        "command_sub": command_sub(tokens),
        "targets": targets(arguments, canonical, tokens),
        "gate_bypass": gate_bypass(canonical),
        "workdir": redact_home(workdir or _text(envelope, "cwd")),
    }


def tool_failure_attrs(envelope: Envelope) -> Attrs:
    """Derive ``tool_failure`` attributes; the error text becomes a signature only."""
    return {
        "tool_use_id": _text(envelope, "tool_use_id"),
        "tool_name": _text(envelope, "tool_name"),
        "failure_signature": failure_signature(_text(envelope, "error")),
        "interrupted": int(envelope.get("is_interrupt") is True),
    }


# --- Turn state -------------------------------------------------------------------


def _state_path(context: Context, producer: str, session_id: str) -> Path:
    digest = hashlib.sha256(f"{producer}\0{session_id}".encode()).hexdigest()[:32]
    return context.state_dir / f"{digest}.turn"


def turn_open(context: Context, producer: str, session_id: str) -> int:
    """Return 1 when this session's turn marker exists and is not stale."""
    path = _state_path(context, producer, session_id)
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return 0
    return int(age < TURN_STALE_SECONDS)


def open_turn(context: Context, producer: str, session_id: str, prompt_id: str) -> None:
    """Mark this session's turn as running."""
    context.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = {"prompt_id": prompt_id, "opened_at": timestamp(context.now())}
    _atomic_write(_state_path(context, producer, session_id), json.dumps(marker) + "\n")


def close_turn(context: Context, producer: str, session_id: str) -> None:
    """Clear this session's running-turn marker."""
    _state_path(context, producer, session_id).unlink(missing_ok=True)


# --- Prompts ----------------------------------------------------------------------


def _prompt_records(
    head: tuple[str, str], envelope: Envelope, context: Context, occurred_at: str
) -> Iterator[Record]:
    producer, session_id = head
    prompt_id = (
        _text(envelope, "prompt_id")
        or hashlib.sha256(f"{producer}\0{session_id}\0{occurred_at}".encode()).hexdigest()[:16]
    )
    attrs: Attrs = {
        "prompt_id": prompt_id,
        "prompt_length": len(_text(envelope, "prompt")),
        "turn_open": turn_open(context, producer, session_id),
    }
    open_turn(context, producer, session_id, prompt_id)
    yield record((producer, session_id, "prompt_submitted"), occurred_at, prompt_id, attrs)


# --- Claude transcript ------------------------------------------------------------


def _is_prompt_entry(entry: dict[str, Any], prompt_id: str) -> bool:
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isSidechain"):
        return False
    if prompt_id:
        return entry.get("promptId") == prompt_id
    message = entry.get("message")
    return (
        isinstance(message, dict)
        and isinstance(message.get("content"), str)
        and "toolUseResult" not in entry
    )


def _usage_fields(entry: dict[str, Any]) -> tuple[str, str, float] | None:
    message = entry.get("message")
    if (
        entry.get("type") != "assistant"
        or entry.get("isSidechain")
        or not isinstance(message, dict)
    ):
        return None
    usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
    details = usage.get("output_tokens_details") if isinstance(usage, dict) else None
    thinking = details.get("thinking_tokens", 0) if isinstance(details, dict) else 0
    model = message.get("model")
    return (
        str(message.get("id") or entry.get("uuid") or ""),
        model if isinstance(model, str) else "",
        float(thinking) if isinstance(thinking, int | float) else 0.0,
    )


def _entries(transcript: Path) -> Iterator[dict[str, Any]]:
    with transcript.open(encoding="utf-8", errors="replace") as lines:
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                yield entry


def _summarize(turn: dict[str, tuple[str, float]]) -> tuple[float, str]:
    models = [model for model, _ in turn.values() if model and model != "<synthetic>"]
    return sum(tokens for _, tokens in turn.values()), (models[-1] if models else "")


def transcript_usage(transcript: Path, prompt_id: str) -> tuple[float, str]:
    """Sum ``thinking_tokens`` and take the last model over the turn's assistant messages.

    The turn starts at the first user entry carrying ``prompt_id``; when no entry
    carries it, the turn starts after the last typed user prompt. Only numeric usage
    and the model name are read; streamed entries sharing a message ID count once.
    """
    by_prompt: dict[str, tuple[str, float]] | None = None
    since_last: dict[str, tuple[str, float]] = {}
    for entry in _entries(transcript):
        if by_prompt is None and prompt_id and _is_prompt_entry(entry, prompt_id):
            by_prompt = {}
        if _is_prompt_entry(entry, ""):
            since_last = {}
        fields = _usage_fields(entry)
        if fields is None:
            continue
        since_last[fields[0]] = fields[1:]
        if by_prompt is not None:
            by_prompt[fields[0]] = fields[1:]
    return _summarize(by_prompt if by_prompt is not None else since_last)


def _turn_stop_attrs(envelope: Envelope) -> Attrs:
    prompt_id = _text(envelope, "prompt_id")
    path = _text(envelope, "transcript_path")
    reasoning, model = transcript_usage(Path(path), prompt_id) if path else (0.0, "")
    return {"prompt_id": prompt_id, "reasoning_tokens": reasoning, "response_model": model}


def _error_class(envelope: Envelope) -> str:
    error = envelope.get("error")
    if isinstance(error, dict):
        error = error.get("type")
    return error if isinstance(error, str) and _ERROR_CLASS.match(error) else "unknown"


# --- Dispatch ---------------------------------------------------------------------


def _claude(event: str, envelope: Envelope, context: Context, occurred_at: str) -> Iterator[Record]:
    session_id = _session(envelope)
    head = ("claude-code", session_id)
    prompt_id = _text(envelope, "prompt_id")
    if event == "UserPromptSubmit":
        yield from _prompt_records(head, envelope, context, occurred_at)
        return
    if event in ("Stop", "StopFailure"):
        close_turn(context, *head)
    simple: dict[str, tuple[str, Callable[[Envelope], Attrs], str]] = {
        "PreToolUse": ("tool_call", tool_call_attrs, "tool_use_id"),
        "PostToolUseFailure": ("tool_failure", tool_failure_attrs, "tool_use_id"),
        "Stop": ("turn_stop", _turn_stop_attrs, "prompt_id"),
        "StopFailure": (
            "turn_failure",
            lambda env: {"prompt_id": prompt_id, "error_class": _error_class(env)},
            "prompt_id",
        ),
        "SubagentStart": (
            "subagent_start",
            lambda env: {
                "agent_id": _text(env, "agent_id"),
                "agent_type": _text(env, "agent_type"),
                "prompt_id": prompt_id,
            },
            "agent_id",
        ),
    }
    if event not in simple:
        raise ValueError(f"unsupported claude-code hook: {event}")
    event_type, derive, key = simple[event]
    yield record((*head, event_type), occurred_at, _text(envelope, key), derive(envelope))


def _omp_simple(event: str, envelope: Envelope) -> tuple[str, str, Attrs] | None:
    if event == "tool_call":
        return "tool_call", _text(envelope, "tool_use_id"), tool_call_attrs(envelope)
    if event == "tool_approval_resolved":
        tool_use_id = _text(envelope, "tool_use_id")
        attrs: Attrs = {
            "tool_use_id": tool_use_id,
            "tool_name": _text(envelope, "tool_name"),
            "approved": int(envelope.get("approved") is True),
        }
        return "approval", tool_use_id, attrs
    if event in ("auto_retry_start", "auto_retry_end"):
        attempt = envelope.get("attempt")
        phase = event.removeprefix("auto_retry_")
        number = attempt if isinstance(attempt, int | float) else 0
        retry: Attrs = {"attempt": number, "phase": phase}
        if phase == "end":
            retry["success"] = int(envelope.get("success") is True)
        return "retry", f"{phase}:{number}", retry
    return None


def _omp(event: str, envelope: Envelope, context: Context, occurred_at: str) -> Iterator[Record]:
    session_id = _session(envelope)
    if event == "input":
        if envelope.get("idle") is False:
            attrs = {"prompt_length": len(_text(envelope, "text"))}
            yield record(("omp", session_id, "steer"), occurred_at, "", attrs)
        return
    simple = _omp_simple(event, envelope)
    if simple is None:
        raise ValueError(f"unsupported omp hook: {event}")
    event_type, key, attrs = simple
    yield record(("omp", session_id, event_type), occurred_at, key, attrs)


_PRODUCERS = {"claude-code": _claude, "omp": _omp}


def iter_records(
    producer: str, hook_event: str, envelope: Envelope, context: Context | None = None
) -> Iterator[Record]:
    """Yield records as they are ready (a prompt's label follows its submission)."""
    if producer not in _PRODUCERS:
        raise ValueError(f"producer must be one of {', '.join(PRODUCERS)}")
    context = context or Context()
    yield from _PRODUCERS[producer](hook_event, envelope, context, timestamp(context.now()))


def normalize(
    producer: str, hook_event: str, envelope: Envelope, context: Context | None = None
) -> list[Record]:
    """Return every record one hook envelope produces."""
    return list(iter_records(producer, hook_event, envelope, context))


# --- Output -----------------------------------------------------------------------


def _atomic_write(destination: Path, content: str) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write(item: Record, inbox: Path | None = None) -> Path:
    """Write one record to the inbox atomically (temporary file, then rename)."""
    inbox = inbox or INBOX
    inbox.mkdir(parents=True, exist_ok=True)
    destination = inbox / f"{item['event_id']}.json"
    _atomic_write(destination, json.dumps(item, separators=(",", ":")) + "\n")
    return destination


def _log(producer: str, hook_event: str, problem: str) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(f"{timestamp(datetime.now(UTC))} {producer} {hook_event} {problem}\n")
    except OSError:
        pass


def main(producer: str, hook_event: str) -> int:
    """Read one envelope from stdin, write its records, and always return 0.

    A failure is logged by exception type only, never with envelope text.
    """
    try:
        envelope = json.loads(sys.stdin.read())
        if not isinstance(envelope, dict):
            raise ValueError("envelope must be a JSON object")
        for item in iter_records(producer, hook_event, envelope):
            write(item)
    except Exception as exc:
        _log(producer[:32], hook_event[:64], type(exc).__name__)
    return 0

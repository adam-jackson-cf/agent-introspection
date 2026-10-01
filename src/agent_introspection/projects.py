"""Session-to-project attribution from the harness session-context hooks.

The hooks installed in Claude Code, Codex, and omp write one JSON file per
session event into the inbox: the session ID and the git project it runs in,
or a rejection with its reason. The activity hooks (``hooks.py``) write
normalized ``agent-introspection.hook-event/1`` records into the same inbox.
``sync`` copies those files into ``introspection.session_projects`` /
``session_project_rejections`` / ``hook_events`` and removes each file once
ClickHouse has it. A launchd job runs ``facts sync`` every minute,
which syncs projects and then refreshes workflow findings.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from agent_introspection.config import SigNozConfig, load_config
from agent_introspection.facts import DATABASE, SqlRunner, sql_string

INBOX = Path.home() / ".local/share/agent-introspection/session-context-inbox"
LABEL = "com.adamjackson.agent-introspection.projects"
PLIST = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
LOG = Path.home() / ".local/share/agent-introspection/projects-sync.log"
INTERVAL_SECONDS = 60
# Rows per INSERT: about 400 bytes each stays under ClickHouse's 256 KiB query limit.
# Larger rows (hook events with many targets) are also cut by encoded size.
_CHUNK = 300
_CHUNK_BYTES = 192 * 1024
_HOME = re.compile(r"^/Users/[^/]+")

HOOK_EVENT_SCHEMA = "agent-introspection.hook-event/1"

type Row = dict[str, Any]


def _redact(root: str) -> str:
    return _HOME.sub("~", root)


def event_row(document: dict[str, Any]) -> Row:
    """Flatten one hook event into a ``session_projects`` row."""
    project = document["agent"]["project"]
    return {
        "event_id": str(document["event_id"]),
        "producer": str(document["producer"]),
        "session_id": str(document["session_id"]),
        "event_type": str(document["event_type"]),
        "occurred_at": str(document["occurred_at"]),
        "project_id": str(project["id"]),
        "project_name": str(project["name"]),
        "project_root": _redact(str(project["root"])),
    }


def rejection_row(document: dict[str, Any]) -> Row:
    """Flatten one hook rejection into a ``session_project_rejections`` row."""
    return {
        "rejection_id": str(document["rejection_id"]),
        "producer": str(document["producer"]),
        "correlation_id": str(document["correlation_id"]),
        "lifecycle_event": str(document["lifecycle_event"]),
        "occurred_at": str(document["occurred_at"]),
        "reason_code": str(document["reason_code"]),
    }


def hook_event_row(document: dict[str, Any]) -> Row:
    """Flatten one activity-hook record into a ``hook_events`` row.

    Numbers and 0/1 flags go to ``attrs_number``; text goes to ``attrs_string``,
    with lists (targets) stored as JSON text.
    """
    attrs = document["attrs"]
    if not isinstance(attrs, dict):
        raise TypeError("attrs must be an object")
    strings: dict[str, str] = {}
    numbers: dict[str, float] = {}
    for key, value in attrs.items():
        if isinstance(value, bool | int | float):
            numbers[str(key)] = float(value)
        elif isinstance(value, str):
            strings[str(key)] = value
        else:
            strings[str(key)] = json.dumps(value, ensure_ascii=False)
    return {
        "event_id": str(document["event_id"]),
        "producer": str(document["producer"]),
        "session_id": str(document["session_id"]),
        "event_type": str(document["event_type"]),
        "occurred_at": str(document["occurred_at"]),
        "attrs_string": strings,
        "attrs_number": numbers,
    }


def ledger_rows(ledger: Path) -> list[Row]:
    """Read the session-context events the retired scan had ingested."""
    connection = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT event_id, producer, session_id, event_type, occurred_at, "
            "project_id, project_name, project_root FROM session_context_events"
        ).fetchall()
    finally:
        connection.close()
    keys = ("event_id", "producer", "session_id", "event_type", "occurred_at")
    return [
        {
            **dict(zip(keys, map(str, row[:5]), strict=True)),
            "project_id": str(row[5]),
            "project_name": str(row[6]),
            "project_root": _redact(str(row[7])),
        }
        for row in rows
    ]


def _chunks(lines: list[str]) -> list[list[str]]:
    chunks: list[list[str]] = []
    size = 0
    for line in lines:
        if not chunks or len(chunks[-1]) >= _CHUNK or size + len(line) > _CHUNK_BYTES:
            chunks.append([])
            size = 0
        chunks[-1].append(line)
        size += len(line) + 1
    return chunks


# Column types for tables whose rows JSON inference would misread: nested objects
# would become tuples, not the Map columns the table declares.
_STRUCTURES = {
    "hook_events": (
        "event_id String, producer String, session_id String, event_type String, "
        "occurred_at DateTime64(6, 'UTC'), attrs_string Map(String, String), "
        "attrs_number Map(String, Float64)"
    ),
    "prompt_labels": (
        "harness String, session_id String, prompt_key String, prompt_id String, "
        "ts DateTime64(9, 'UTC'), prompt_length UInt32, attempts UInt8, "
        "classified_at DateTime64(3, 'UTC'), status String, reason String, "
        "task_type String, task_type_confidence Float32, correction Float32, "
        "correction_kind String, sentiment String, sentiment_confidence Float32, "
        "classifier_model String, classifier_version UInt16, cost_usd Float64"
    ),
}


def insert_statements(table: str, rows: list[Row]) -> list[str]:
    """Return chunked INSERTs that parse rows as JSONEachRow inside ClickHouse."""
    structure = f"{sql_string(_STRUCTURES[table])}, " if table in _STRUCTURES else ""
    columns = ", ".join(rows[0])
    # Columns are selected by name, so the declared structure's order never matters.
    return [
        f"INSERT INTO {DATABASE}.{table} ({columns}) "
        f"SELECT {columns} FROM format(JSONEachRow, {structure}"
        + sql_string("\n".join(chunk))
        # Hook timestamps are RFC 3339 with an offset.
        + ") SETTINGS date_time_input_format = 'best_effort'"
        for chunk in _chunks([json.dumps(row) for row in rows])
    ]


type Loaded = list[tuple[Path, Row]]


def _read_inbox(inbox: Path) -> tuple[dict[str, Loaded], int]:
    """Sort inbox files into rows per table; unreadable files are counted as invalid."""
    loaded: dict[str, Loaded] = {
        "session_projects": [],
        "session_project_rejections": [],
        "hook_events": [],
    }
    invalid = 0
    for path in sorted(inbox.glob("*.json")):
        try:
            document = json.loads(path.read_text())
            if document.get("schema") == HOOK_EVENT_SCHEMA:
                loaded["hook_events"].append((path, hook_event_row(document)))
            elif "rejection_id" in document:
                loaded["session_project_rejections"].append((path, rejection_row(document)))
            else:
                loaded["session_projects"].append((path, event_row(document)))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            invalid += 1
    return loaded, invalid


def sync(run: SqlRunner, *, inbox: Path = INBOX, ledger: Path | None = None) -> dict[str, Any]:
    """Load hook events into ClickHouse, then remove the inbox files it now holds.

    Files that fail to parse stay in the inbox and are counted as invalid.
    ``ledger`` imports the history the retired scan had already ingested.
    """
    loaded, invalid = _read_inbox(inbox)
    history = ledger_rows(ledger) if ledger is not None else []
    for table, files in loaded.items():
        rows = [row for _, row in files] + (history if table == "session_projects" else [])
        if rows:
            for statement in insert_statements(table, rows):
                run(statement)
    for files in loaded.values():
        for path, _ in files:
            path.unlink(missing_ok=True)
    return {
        "events": len(loaded["session_projects"]),
        "rejections": len(loaded["session_project_rejections"]),
        "hook_events": len(loaded["hook_events"]),
        "ledger_events": len(history),
        "invalid": invalid,
    }


def plist(executable: Path, path_dirs: Iterable[str], config: Path | None = None) -> bytes:
    """Return the launchd job that runs ``facts sync`` (projects, then findings) every minute.

    A selected ``config`` is passed through, so the job syncs the same deployment.
    """
    selected = ["--config", str(config)] if config is not None else []
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [str(executable), *selected, "facts", "sync"],
        "StartInterval": INTERVAL_SECONDS,
        "RunAtLoad": True,
        "EnvironmentVariables": {"PATH": ":".join(dict.fromkeys(path_dirs))},
        "StandardOutPath": str(LOG),
        "StandardErrorPath": str(LOG),
    })


def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(("launchctl", *args), capture_output=True, text=True, check=False)


def _job_path(signoz: SigNozConfig) -> list[str]:
    """Return the tool directories the launchd job needs on PATH.

    Docker mode needs the docker CLI. HTTP mode needs the password command's
    executable, because launchd cannot pass the password variable to the job. omp's
    directory lets prompt labelling read omp's stored OpenRouter credential
    (`omp token openrouter`) when OPENROUTER_API_KEY is not set for the job.
    """
    required: list[str] = []
    if signoz.mode == "docker":
        required.append("docker")
    elif signoz.clickhouse_password_env is not None:
        raise RuntimeError(
            "launchd cannot pass signoz.clickhouse_password_env to the job; "
            "use signoz.clickhouse_password_command for a scheduled sync"
        )
    elif signoz.clickhouse_password_command is not None:
        required.append(signoz.clickhouse_password_command[0])
    tools: list[str] = []
    for name in required:
        found = shutil.which(name)
        if found is None:
            raise RuntimeError(f"{name} not found on PATH")
        tools.append(str(Path(found).parent))
    omp = shutil.which("omp")
    return [*tools, *([str(Path(omp).parent)] if omp else [])]


def schedule_install(config: Path | None = None) -> dict[str, Any]:
    """Write and load the launchd job that runs `facts sync` every minute."""
    executable = Path(sys.executable).with_name("agent-introspection")
    tools = _job_path(load_config(config).signoz)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    PLIST.write_bytes(plist(executable, (*tools, "/usr/bin", "/bin", "/usr/sbin", "/sbin"), config))
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", f"{domain}/{LABEL}")
    loaded = _launchctl("bootstrap", domain, str(PLIST))
    if loaded.returncode != 0:
        raise RuntimeError(loaded.stderr.strip() or "launchctl bootstrap failed")
    return {"installed": True, "label": LABEL, "interval_seconds": INTERVAL_SECONDS}


def schedule_remove() -> dict[str, Any]:
    """Unload the launchd job and delete its plist."""
    _launchctl("bootout", f"gui/{os.getuid()}/{LABEL}")
    existed = PLIST.exists()
    PLIST.unlink(missing_ok=True)
    return {"removed": existed, "label": LABEL}


def schedule_status() -> dict[str, Any]:
    """Report whether the job is loaded and how many inbox files wait."""
    listed = _launchctl("list", LABEL)
    backlog = len(list(INBOX.glob("*.json"))) if INBOX.exists() else 0
    return {
        "installed": PLIST.exists(),
        "loaded": listed.returncode == 0,
        "inbox_backlog": backlog,
    }

"""Session-to-project attribution from the harness session-context hooks.

The hooks installed in Claude Code, Codex, and omp write one JSON file per
session event into the inbox: the session ID and the git project it runs in,
or a rejection with its reason. ``sync`` copies those files into
``introspection.session_projects`` / ``session_project_rejections`` and removes
each file once ClickHouse has it. A launchd job runs ``facts sync`` every minute,
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

from agent_introspection.facts import DATABASE, SqlRunner, sql_string

INBOX = Path.home() / ".local/share/agent-introspection/session-context-inbox"
LABEL = "com.adamjackson.agent-introspection.projects"
PLIST = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
LOG = Path.home() / ".local/share/agent-introspection/projects-sync.log"
INTERVAL_SECONDS = 60
# Rows per INSERT: about 400 bytes each stays under ClickHouse's 256 KiB query limit.
_CHUNK = 300
_HOME = re.compile(r"^/Users/[^/]+")

type Row = dict[str, str]


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


def insert_statements(table: str, rows: list[Row]) -> list[str]:
    """Return chunked INSERTs that parse rows as JSONEachRow inside ClickHouse."""
    return [
        f"INSERT INTO {DATABASE}.{table} ({', '.join(rows[0])}) "
        f"SELECT * FROM format(JSONEachRow, "
        + sql_string("\n".join(json.dumps(row) for row in rows[start : start + _CHUNK]))
        # Hook timestamps are RFC 3339 with an offset.
        + ") SETTINGS date_time_input_format = 'best_effort'"
        for start in range(0, len(rows), _CHUNK)
    ]


def _read_inbox(inbox: Path) -> tuple[list[tuple[Path, Row]], list[tuple[Path, Row]], int]:
    events: list[tuple[Path, Row]] = []
    rejections: list[tuple[Path, Row]] = []
    invalid = 0
    for path in sorted(inbox.glob("*.json")):
        try:
            document = json.loads(path.read_text())
            if "rejection_id" in document:
                rejections.append((path, rejection_row(document)))
            else:
                events.append((path, event_row(document)))
        except (OSError, ValueError, KeyError, TypeError):
            invalid += 1
    return events, rejections, invalid


def sync(run: SqlRunner, *, inbox: Path = INBOX, ledger: Path | None = None) -> dict[str, Any]:
    """Load hook events into ClickHouse, then remove the inbox files it now holds.

    Files that fail to parse stay in the inbox and are counted as invalid.
    ``ledger`` imports the history the retired scan had already ingested.
    """
    events, rejections, invalid = _read_inbox(inbox) if inbox.exists() else ([], [], 0)
    history = ledger_rows(ledger) if ledger is not None else []
    for table, rows in (
        ("session_projects", [row for _, row in events] + history),
        ("session_project_rejections", [row for _, row in rejections]),
    ):
        if rows:
            for statement in insert_statements(table, rows):
                run(statement)
    for path, _ in (*events, *rejections):
        path.unlink(missing_ok=True)
    return {
        "events": len(events),
        "rejections": len(rejections),
        "ledger_events": len(history),
        "invalid": invalid,
    }


def plist(executable: Path, path_dirs: Iterable[str], config: Path | None = None) -> bytes:
    """Return the launchd job that runs ``facts sync`` (projects, then findings) every minute.

    A selected ``config`` is passed through, so the job syncs the same deployment.
    """
    selected = ["--config", str(config)] if config is not None else []
    return plistlib.dumps(
        {
            "Label": LABEL,
            "ProgramArguments": [str(executable), *selected, "facts", "sync"],
            "StartInterval": INTERVAL_SECONDS,
            "RunAtLoad": True,
            "EnvironmentVariables": {"PATH": ":".join(dict.fromkeys(path_dirs))},
            "StandardOutPath": str(LOG),
            "StandardErrorPath": str(LOG),
        }
    )


def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(("launchctl", *args), capture_output=True, text=True, check=False)


def schedule_install(config: Path | None = None) -> dict[str, Any]:
    """Write and load the launchd job; the docker CLI directory joins its PATH."""
    executable = Path(sys.executable).with_name("agent-introspection")
    docker = shutil.which("docker")
    if docker is None:
        raise RuntimeError("docker CLI not found on PATH")
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    PLIST.write_bytes(
        plist(
            executable,
            (str(Path(docker).parent), "/usr/bin", "/bin", "/usr/sbin", "/sbin"),
            config,
        )
    )
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

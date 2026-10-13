"""Session-to-project attribution from the harnesses' own session stores.

Every harness keeps its sessions on disk with the session ID its telemetry reports
and the working directory the session ran in (``session_stores.toml`` says where and
in which record). ``resolve`` reads only that record from each new session file, or
only the store's query from a SQLite store's database, resolves the working
directory to its Git project on this machine, and spools one ``session-project/1``
record per session (``spool.py`` ships it to the facts store):
``attributed`` with the project, or the reason it has none (``non_git_workspace``,
``missing_workspace``). A project with a safe ``host/path`` remote has the same ID on
every machine; one without is keyed by host and root.
When the working directory is gone (a deleted worktree, a moved repository) and the
store records the session's Git remote, the session is attributed to the repository
on this machine with that remote (``resolved_by = 'remote'``); paths are never guessed.
No harness hook or configuration is involved, and message content is never read.

Processed files are remembered in ``SCAN_STATE`` so each minute only new files are
read; a file whose record has not been written yet is retried. A SQLite database
holds many sessions and keeps growing, so it is queried on every scan and its
processed session IDs are remembered instead. A database that cannot be read
(locked, corrupt, or without the queried table) is reported in the scan result and
retried on the next scan. ``rescan`` forgets the state; the loaders keep the newest
record per session.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import tomllib
from collections.abc import Iterable, Iterator, Mapping
from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from importlib.resources import files
from itertools import islice
from pathlib import Path
from typing import Any

from agent_introspection import hooks, spool
from agent_introspection.json_types import JsonMapping

DATA_DIR = Path.home() / ".local/share/agent-introspection"
SCAN_STATE = DATA_DIR / "session-scan.json"
_HOME = re.compile(r"^/(?:Users|home)/[^/]+|^/root(?=/|$)")
_SAFE_REMOTE = re.compile(r"[a-z0-9][a-z0-9-]*(\.[a-z0-9-]+)+/[^\s@\\?#]+")
_VARIABLE = re.compile(r"\$(\w+)")
_GIT_TIMEOUT_SECONDS = 10
# A harness holding a write lock is waited on this long; the database is then
# reported unreadable and retried on the next scan.
_SQLITE_TIMEOUT_SECONDS = 2.0
_SQLITE_HEADER = b"SQLite format 3\x00"
# Header bytes 18 and 19 (file format write and read versions) are 2 in WAL mode.
_WAL_VERSIONS = b"\x02\x02"
_QUERY_COLUMNS = ("session_id", "cwd", "started_at")

type Row = dict[str, Any]


class SessionStoreError(ValueError):
    """Raised when a session store entry or its query result is malformed."""


class StoreFormat(StrEnum):
    """How a store keeps its sessions."""

    JSONL = "jsonl"  # one JSON Lines file per session
    SQLITE = "sqlite"  # one database holding many sessions


@dataclass(frozen=True)
class Store:
    """Where one harness keeps its sessions and which record identifies a session.

    A JSONL store names the record by ``glob``, ``max_lines``, ``match``, and dotted
    paths; a SQLite store names it by ``query``, whose result columns are
    ``session_id``, ``cwd``, ``started_at``, and optionally ``remote``.
    """

    name: str
    harnesses: tuple[str, ...]
    roots: tuple[str, ...]
    glob: str = ""
    max_lines: int = 0
    match: JsonMapping = field(default_factory=dict)
    session_id: str = ""
    cwd: str = ""
    started_at: str = ""
    remote: str = ""
    format: StoreFormat = StoreFormat.JSONL
    query: str = ""


@dataclass(frozen=True)
class Session:
    """One session as its store records it."""

    session_id: str
    cwd: str
    started_at: str
    remote: str = ""


def load_stores(extra_roots: Mapping[str, Iterable[str]] | None = None) -> list[Store]:
    """Return the session stores, with any roots the user's config adds."""
    document = tomllib.loads(
        files("agent_introspection").joinpath("session_stores.toml").read_text()
    )
    extra = extra_roots or {}
    return [parse_store(entry, extra.get(entry.get("name"), ())) for entry in document["store"]]


_COMMON_KEYS = frozenset({"name", "harnesses", "roots", "format"})
_REQUIRED_KEYS = {
    StoreFormat.JSONL: frozenset({"glob", "max_lines", "match", "session_id", "cwd", "started_at"}),
    StoreFormat.SQLITE: frozenset({"query"}),
}
_OPTIONAL_KEYS = {StoreFormat.JSONL: frozenset({"remote"}), StoreFormat.SQLITE: frozenset()}


def parse_store(entry: Mapping[str, Any], extra_roots: Iterable[str] = ()) -> Store:
    """Validate one ``[[store]]`` entry and build its store, ``extra_roots`` appended.

    This is the one place a store entry is checked, for ``session_stores.toml`` and
    for a candidate harness profile's inline ``[session_store_spec]`` alike.

    Raises
    ------
    SessionStoreError
        When a key is missing, does not apply to the store's format, or has the
        wrong type.
    """
    if not isinstance(entry, Mapping):
        raise SessionStoreError("a session store must be a table")
    name = _text(entry, "name", "session store")
    label = f"session store {name!r}"
    store_format = _store_format(entry, label)
    _check_keys(entry, store_format, label)
    store = Store(
        name=name,
        harnesses=_texts(entry, "harnesses", label),
        roots=(*_texts(entry, "roots", label), *extra_roots),
        format=store_format,
    )
    if store_format is StoreFormat.SQLITE:
        return replace(store, query=_text(entry, "query", label))
    return replace(
        store,
        glob=_text(entry, "glob", label),
        max_lines=_positive(entry, "max_lines", label),
        match=_table(entry, "match", label),
        session_id=_text(entry, "session_id", label),
        cwd=_text(entry, "cwd", label),
        started_at=_text(entry, "started_at", label),
        remote=_text(entry, "remote", label) if "remote" in entry else "",
    )


def _store_format(entry: Mapping[str, Any], label: str) -> StoreFormat:
    value = entry.get("format", StoreFormat.JSONL.value)
    try:
        return StoreFormat(value)
    except ValueError:
        choices = ", ".join(member.value for member in StoreFormat)
        raise SessionStoreError(f"{label}: format must be one of {choices}") from None


def _check_keys(entry: Mapping[str, Any], store_format: StoreFormat, label: str) -> None:
    required = _REQUIRED_KEYS[store_format]
    missing = sorted(required - set(entry))
    if missing:
        raise SessionStoreError(f"{label}: missing {', '.join(missing)}")
    unknown = sorted(set(entry) - _COMMON_KEYS - required - _OPTIONAL_KEYS[store_format])
    if unknown:
        raise SessionStoreError(
            f"{label}: {', '.join(unknown)} not used by a {store_format.value} store"
        )


def _text(entry: Mapping[str, Any], key: str, label: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SessionStoreError(f"{label}: {key} must be a non-empty string")
    return value


def _texts(entry: Mapping[str, Any], key: str, label: str) -> tuple[str, ...]:
    value = entry.get(key)
    if (
        not isinstance(value, list | tuple)
        or not value
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise SessionStoreError(f"{label}: {key} must be a non-empty list of strings")
    return tuple(value)


def _positive(entry: Mapping[str, Any], key: str, label: str) -> int:
    value = entry.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SessionStoreError(f"{label}: {key} must be a positive integer")
    return value


def _table(entry: Mapping[str, Any], key: str, label: str) -> JsonMapping:
    value = entry.get(key)
    if not isinstance(value, Mapping) or not all(isinstance(path, str) for path in value):
        raise SessionStoreError(f"{label}: {key} must be a table of dotted paths to values")
    return value


def _expand(root: str) -> list[Path]:
    names = _VARIABLE.findall(root)
    if any(not os.environ.get(name) for name in names):
        return []
    expanded = os.path.expanduser(os.path.expandvars(root))
    return [Path(path) for path in sorted(glob.glob(expanded))]


def expand_root(root: str) -> list[Path]:
    """Expand ``~``, ``$VAR``, and glob patterns; an unset variable yields nothing."""
    return [path for path in _expand(root) if path.is_dir()]


def _field(record: Mapping[str, Any], dotted: str) -> Any:
    value: Any = record
    for part in dotted.split("."):
        if not isinstance(value, Mapping):
            return None
        value = value.get(part)
    return value


def read_session(path: Path, store: Store) -> Session | None:
    """Return the session the file records, or None when its record is not there yet."""
    try:
        with path.open("rb") as handle:
            for line in islice(handle, store.max_lines):
                session = _session_from(line, store)
                if session is not None:
                    return session
    except OSError:
        return None
    return None


def _session_from(line: bytes, store: Store) -> Session | None:
    try:
        record = json.loads(line)
    except ValueError:
        return None
    if not isinstance(record, dict) or any(
        _field(record, key) != value for key, value in store.match.items()
    ):
        return None
    return _session(
        _field(record, store.session_id),
        _field(record, store.cwd),
        _field(record, store.started_at),
        _field(record, store.remote) if store.remote else None,
    )


def _session(session_id: object, cwd: object, started: object, remote: object) -> Session | None:
    """Build a session when the record has a session ID and a working directory."""
    if not isinstance(session_id, str) or not session_id or not isinstance(cwd, str):
        return None
    return Session(
        session_id,
        cwd,
        started if isinstance(started, str) else "",
        remote if isinstance(remote, str) else "",
    )


def read_database(path: Path, store: Store) -> list[Session]:
    """Return the sessions a SQLite store's query finds in one database.

    The database is opened read-only (``_database_uri``) and only the store's query
    runs. A row without a text session ID or working directory is skipped, so its
    session is read once the harness has written it.

    Raises
    ------
    sqlite3.Error
        When the database cannot be read: missing, locked past the timeout, corrupt,
        without the queried table, or the query tries to write.
    SessionStoreError
        When the query does not return the ``session_id``, ``cwd``, and
        ``started_at`` columns.
    """
    uri = _database_uri(path)
    with closing(sqlite3.connect(uri, uri=True, timeout=_SQLITE_TIMEOUT_SECONDS)) as connection:
        cursor = connection.execute(store.query)
        columns = [column[0] for column in cursor.description or ()]
        missing = [name for name in _QUERY_COLUMNS if name not in columns]
        if missing:
            raise SessionStoreError(
                f"session store {store.name!r}: query returns no {', '.join(missing)} column"
            )
        records = (dict(zip(columns, values, strict=True)) for values in cursor)
        found = (
            _session(
                record["session_id"], record["cwd"], record["started_at"], record.get("remote")
            )
            for record in records
        )
        return [session for session in found if session is not None]


def _database_uri(path: Path) -> str:
    """Return a read-only SQLite URI for ``path`` that leaves the harness's files alone.

    ``mode=ro`` takes the usual shared locks and reads transactions still in the
    write-ahead log, so it is used for a rollback-journal database and for a WAL
    database with a ``-wal`` file (its harness is running, or stopped uncleanly).
    A WAL database without one was closed cleanly, so every transaction is in the
    main file; ``mode=ro`` would leave new empty ``-wal`` and ``-shm`` files beside
    it, so it is opened ``immutable=1``, which creates no file and takes no lock. A
    harness that starts writing meanwhile writes to a new ``-wal`` first, which the
    next scan reads.
    """
    real = path.resolve()
    uri = f"{real.as_uri()}?mode=ro"
    if _in_wal_mode(real) and not Path(f"{real}-wal").exists():
        return f"{uri}&immutable=1"
    return uri


def _in_wal_mode(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            header = handle.read(20)
    except OSError:
        return False
    return header.startswith(_SQLITE_HEADER) and header[18:20] == _WAL_VERSIONS


@dataclass(frozen=True)
class Project:
    """A working directory's Git project, or the reason it has none."""

    status: str
    project_id: str = ""
    name: str = ""
    root: str = ""
    resolved_by: str = ""
    remote: str = ""


def _run_git(cwd: str, *args: str) -> subprocess.CompletedProcess[str]:
    # Inherited GIT_* variables (for example inside a git hook) would point git at
    # another repository than the session's directory.
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run(
        ["git", "-C", cwd, *args],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
        check=False,
        env=environment,
    )


def resolve_project(cwd: str) -> Project:
    """Resolve a working directory to the root of its Git repository on this machine.

    A linked worktree resolves to its main repository, so every checkout of one
    repository on any machine with the same remote is one project (see ``_project``).
    """
    if not cwd or not os.path.isabs(cwd) or not os.path.isdir(cwd):
        return Project("missing_workspace")
    try:
        result = _run_git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    except (OSError, subprocess.TimeoutExpired):
        return Project("non_git_workspace")
    common = result.stdout.strip()
    if result.returncode != 0 or not common:
        return Project("non_git_workspace")
    return _project(os.path.realpath(os.path.dirname(common.rstrip("/"))), "workspace")


def _project(root: str, resolved_by: str, remote: str | None = None) -> Project:
    """Build an attributed project; ``remote`` is its safe host/path origin, else ''.

    A repository with a safe remote has one ID on every machine and checkout path
    (SHA-256 of ``git-remote`` and the remote); one without is keyed by this host and
    root, so unrelated machines never collide.
    """
    origin = safe_remote(remote_of(root)) if remote is None else remote
    key = f"git-remote\0{origin}" if origin else f"git\0{spool.host_name()}\0{root}"
    return Project(
        "attributed",
        hashlib.sha256(key.encode()).hexdigest(),
        os.path.basename(root),
        _HOME.sub("~", root),
        resolved_by,
        origin,
    )


def normalize_remote(url: str) -> str:
    """Reduce a Git remote URL to host/path, so SSH and HTTPS forms compare equal.

    Userinfo, query string and fragment are dropped: repository identity is host plus
    path, and tokens travel in those other parts (``?access_token=``, ``#token``).
    """
    text = re.split(r"[?#]", url.strip().lower(), maxsplit=1)[0]
    text = text.removesuffix("/").removesuffix(".git")
    text = re.sub(r"^[a-z+]+://", "", text)
    text = re.sub(r"^[^@/]+@", "", text)
    host, _, rest = text.partition(":")
    return f"{host}/{rest}" if rest and "/" not in host else text


def safe_remote(remote: str) -> str:
    """Return ``remote`` when it is a dotted host plus path without credentials, else ''.

    Filesystem remotes (a local bare repository) and anything holding ``@``, ``?`` or
    ``#`` could carry a user name or a secret, so they never leave the machine.
    """
    return remote if _SAFE_REMOTE.fullmatch(remote) else ""


def remote_of(root: str) -> str:
    """Return the normalized `origin` remote of a local repository, or ''."""
    try:
        result = _run_git(root, "remote", "get-url", "origin")
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return normalize_remote(result.stdout) if result.returncode == 0 and result.stdout else ""


@dataclass
class ScanState:
    """What earlier scans read, and what this scan could not.

    ``done`` holds the session files already read, ``database_sessions`` the session
    IDs already read from each SQLite database, and ``remotes`` the Git remotes of
    repositories seen so far. ``unreadable`` lists the databases this scan could not
    read; it is not saved, so they are retried.
    """

    done: set[str] = field(default_factory=set)
    remotes: dict[str, str] = field(default_factory=dict)
    database_sessions: dict[str, set[str]] = field(default_factory=dict)
    unreadable: list[dict[str, str]] = field(default_factory=list)


def _load_state(path: Path) -> ScanState:
    # A state written before SQLite stores existed has no ``database_sessions``.
    try:
        document = json.loads(path.read_text())
        return ScanState(
            set(document["done"]),
            dict(document["remotes"]),
            {key: set(ids) for key, ids in document.get("database_sessions", {}).items()},
        )
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return ScanState()


def _save_state(path: Path, state: ScanState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    document = {
        "done": sorted(state.done),
        "remotes": state.remotes,
        "database_sessions": {
            key: sorted(ids) for key, ids in sorted(state.database_sessions.items())
        },
    }
    temporary.write_text(json.dumps(document))
    temporary.replace(path)


def session_files(store: Store) -> Iterator[Path]:
    """Yield every file of a store that holds sessions, across its roots, once each.

    A JSONL store's roots are directories of session files (``glob``); a SQLite
    store's roots name its database files.
    """
    if store.format == StoreFormat.SQLITE:
        candidates = (path for root in store.roots for path in _expand(root))
    else:
        directories = (directory for root in store.roots for directory in expand_root(root))
        candidates = (path for directory in directories for path in directory.glob(store.glob))
    seen: set[Path] = set()
    for path in candidates:
        real = path.resolve()
        if real not in seen and path.is_file():
            seen.add(real)
            yield path


def _row(store: Store, session: Session, project: Project, resolved_at: str) -> Row:
    """Build the ``session-project/1`` record; its ID is stable for one resolution."""
    identity = (
        spool.SESSION_PROJECT_SCHEMA,
        session.session_id,
        project.status,
        project.project_id,
        resolved_at,
    )
    return {
        "schema": spool.SESSION_PROJECT_SCHEMA,
        "event_id": hashlib.sha256("\0".join(identity).encode()).hexdigest(),
        "session_id": session.session_id,
        "store": store.name,
        "status": project.status,
        "project_id": project.project_id,
        "project_name": project.name,
        "project_root": project.root,
        "resolved_by": project.resolved_by,
        "remote": project.remote,
        "started_at": session.started_at or resolved_at,
        "resolved_at": resolved_at,
    }


_PREFERENCE = {"attributed": 0, "non_git_workspace": 1, "missing_workspace": 2}


def collect(stores: Iterable[Store], state: ScanState) -> list[Row]:
    """Read sessions not yet done and return one row per session.

    ``state`` is updated in place. A session found in several files keeps its best
    result (attributed first). A session whose workspace is gone falls back to its
    recorded remote, matched against every repository sessions have resolved to.
    """
    found: list[tuple[Store, Session]] = []
    for store in stores:
        read = _new_database_sessions if store.format == StoreFormat.SQLITE else _new_file_sessions
        found.extend((store, session) for session in read(store, state))
    resolved_at = hooks.timestamp(datetime.now(UTC))
    projects = {cwd: resolve_project(cwd) for cwd in {session.cwd for _, session in found}}
    _index_remotes(state, (project.root for project in projects.values() if project.root))
    best: dict[str, Row] = {}
    for store, session in found:
        project = projects[session.cwd]
        if project.status == "missing_workspace" and session.remote:
            remote = normalize_remote(session.remote)
            root = state.remotes.get(remote)
            if root is not None and os.path.isdir(root):
                project = _project(root, "remote", safe_remote(remote))
        row = _row(store, session, project, resolved_at)
        current = best.get(session.session_id)
        if current is None or _PREFERENCE[row["status"]] < _PREFERENCE[current["status"]]:
            best[session.session_id] = row
    return list(best.values())


def _new_file_sessions(store: Store, state: ScanState) -> list[Session]:
    """Read each session file not yet done; one without its record yet is retried."""
    found: list[Session] = []
    for path in session_files(store):
        key = str(path)
        if key in state.done:
            continue
        session = read_session(path, store)
        if session is not None:
            found.append(session)
            state.done.add(key)
    return found


def _new_database_sessions(store: Store, state: ScanState) -> list[Session]:
    """Query each database and keep the sessions whose IDs it has not yielded before.

    A database that cannot be read is recorded in ``state.unreadable`` and skipped,
    so the other databases and stores are still read.
    """
    found: list[Session] = []
    for path in session_files(store):
        key = str(path)
        try:
            recorded = read_database(path, store)
        except (sqlite3.Error, SessionStoreError) as error:
            state.unreadable.append({
                "store": store.name,
                "database": _HOME.sub("~", key),
                "error": str(error),
            })
            continue
        done = state.database_sessions.setdefault(key, set())
        new = {
            session.session_id: session for session in recorded if session.session_id not in done
        }
        done.update(new)
        found.extend(new.values())
    return found


def _index_remotes(state: ScanState, roots: Iterable[str]) -> None:
    known = set(state.remotes.values())
    for root in sorted(set(roots)):
        local = os.path.realpath(os.path.expanduser(root))
        if local in known:
            continue
        remote = remote_of(local)
        if remote:
            state.remotes.setdefault(remote, local)


def resolve(
    *,
    stores: Iterable[Store] | None = None,
    state: Path | None = None,
    rescan: bool = False,
    inbox: Path | None = None,
) -> dict[str, Any]:
    """Attribute every session not yet processed and spool one record per session.

    The result counts the sessions by status; ``unreadable`` lists any database this
    scan could not read (store, database, and SQLite's error), which the next scan
    retries.
    """
    selected = list(stores) if stores is not None else load_stores()
    state_path = state if state is not None else SCAN_STATE
    scan = _load_state(state_path)
    if rescan:
        scan.done = set()
        scan.database_sessions = {}
    rows = collect(selected, scan)
    for row in rows:
        hooks.write(row, inbox)
    _save_state(state_path, scan)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    result: dict[str, Any] = {"sessions": len(rows), **counts}
    if scan.unreadable:
        result["unreadable"] = scan.unreadable
    return result

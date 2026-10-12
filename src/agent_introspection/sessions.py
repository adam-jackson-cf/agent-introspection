"""Session-to-project attribution from the harnesses' own session stores.

Every harness keeps its sessions on disk with the session ID its telemetry reports
and the working directory the session ran in (``session_stores.toml`` says where and
in which record). ``resolve`` reads only that record from each new session file,
resolves the working directory to its Git project on this machine, and spools one
``session-project/1`` record per session (``spool.py`` ships it to the facts store):
``attributed`` with the project, or the reason it has none (``non_git_workspace``,
``missing_workspace``). A project with a safe ``host/path`` remote has the same ID on
every machine; one without is keyed by host and root.
When the working directory is gone (a deleted worktree, a moved repository) and the
store records the session's Git remote, the session is attributed to the repository
on this machine with that remote (``resolved_by = 'remote'``); paths are never guessed.
No harness hook or configuration is involved, and message content is never read.

Processed files are remembered in ``SCAN_STATE`` so each minute only new files are
read; a file whose record has not been written yet is retried. ``rescan`` forgets
the state; the loaders keep the newest record per session.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import subprocess
import tomllib
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
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

type Row = dict[str, Any]


@dataclass(frozen=True)
class Store:
    """Where one harness keeps its sessions and which record identifies a session."""

    name: str
    harnesses: tuple[str, ...]
    roots: tuple[str, ...]
    glob: str
    max_lines: int
    match: JsonMapping
    session_id: str
    cwd: str
    started_at: str
    remote: str = ""


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
    return [
        Store(
            name=entry["name"],
            harnesses=tuple(entry["harnesses"]),
            roots=(*entry["roots"], *extra.get(entry["name"], ())),
            glob=entry["glob"],
            max_lines=int(entry["max_lines"]),
            match=entry["match"],
            session_id=entry["session_id"],
            cwd=entry["cwd"],
            started_at=entry["started_at"],
            remote=entry.get("remote", ""),
        )
        for entry in document["store"]
    ]


def expand_root(root: str) -> list[Path]:
    """Expand ``~``, ``$VAR``, and glob patterns; an unset variable yields nothing."""
    names = _VARIABLE.findall(root)
    if any(not os.environ.get(name) for name in names):
        return []
    expanded = os.path.expanduser(os.path.expandvars(root))
    return [Path(path) for path in sorted(glob.glob(expanded)) if Path(path).is_dir()]


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
    session_id = _field(record, store.session_id)
    cwd = _field(record, store.cwd)
    if not isinstance(session_id, str) or not session_id or not isinstance(cwd, str):
        return None
    started = _field(record, store.started_at)
    remote = _field(record, store.remote) if store.remote else None
    return Session(
        session_id,
        cwd,
        started if isinstance(started, str) else "",
        remote if isinstance(remote, str) else "",
    )


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
    """Session files already read, and the Git remotes of repositories seen so far."""

    done: set[str] = field(default_factory=set)
    remotes: dict[str, str] = field(default_factory=dict)


def _load_state(path: Path) -> ScanState:
    try:
        document = json.loads(path.read_text())
        return ScanState(set(document["done"]), dict(document["remotes"]))
    except (OSError, ValueError, TypeError, KeyError):
        return ScanState()


def _save_state(path: Path, state: ScanState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"done": sorted(state.done), "remotes": state.remotes}))
    temporary.replace(path)


def session_files(store: Store) -> Iterator[Path]:
    """Yield every session file of a store across its existing roots, once each."""
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
    """Read sessions from files not yet done and return one row per session.

    ``state`` is updated in place. A session found in several files keeps its best
    result (attributed first). A session whose workspace is gone falls back to its
    recorded remote, matched against every repository sessions have resolved to.
    """
    found: list[tuple[Store, Session]] = []
    finished = state.done
    for store in stores:
        for path in session_files(store):
            key = str(path)
            if key in finished:
                continue
            session = read_session(path, store)
            if session is not None:
                found.append((store, session))
                finished.add(key)
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
    """Attribute every session file not yet processed and spool one record per session."""
    selected = list(stores) if stores is not None else load_stores()
    state_path = state if state is not None else SCAN_STATE
    scan = _load_state(state_path)
    if rescan:
        scan.done = set()
    rows = collect(selected, scan)
    for row in rows:
        hooks.write(row, inbox)
    _save_state(state_path, scan)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {"sessions": len(rows), **counts}

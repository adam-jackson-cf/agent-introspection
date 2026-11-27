import os
import pwd
import shutil
import sys
import tempfile
from collections.abc import Callable, Generator, Iterator
from pathlib import Path

import pytest
from pluggy import Result

RULE = "TEST.user-data-guard"
REDIRECTED = ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME")
HOME_OVERRIDES = ("CLAUDE_CONFIG_DIR", "CODEX_HOME")
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
PATH_EVENTS = {
    "os.chmod": (0,),
    "os.chown": (0,),
    "os.link": (0, 1),
    "os.mkdir": (0,),
    "os.remove": (0,),
    "os.rename": (0, 1),
    "os.rmdir": (0,),
    "os.symlink": (1,),
    "os.truncate": (0,),
    "os.utime": (0,),
    "shutil.copyfile": (1,),
    "shutil.copytree": (1,),
    "shutil.rmtree": (0,),
    "sqlite3.connect": (0,),
}


def wrapper_hook[Hook: Callable[..., object]](function: Hook) -> Hook:
    marked: Hook = pytest.hookimpl(hookwrapper=True)(function)
    return marked


class UserDataGuardError(PermissionError):
    pass


def _absolute(raw: object) -> str | None:
    if isinstance(raw, bytes):
        raw = os.fsdecode(raw)
    if isinstance(raw, os.PathLike):
        raw = os.fspath(raw)
    if not isinstance(raw, str) or raw in {"", ":memory:"} or raw.startswith("file:"):
        return None
    return os.path.abspath(raw)


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


class UserDataGuard:
    def __init__(self, real_home: Path, allowed: tuple[Path, ...]) -> None:
        self.real_home = (str(real_home), os.path.realpath(real_home))
        self.allowed = tuple(
            root for path in allowed for root in (str(path), os.path.realpath(path))
        )
        self.home = Path(tempfile.mkdtemp(prefix="agent-introspection-home-"))
        self.violations: list[str] = []
        self.current: list[str] = []
        self.item = ""

    def redirect(self) -> None:
        os.environ["HOME"] = str(self.home)
        for name in REDIRECTED:
            directory = self.home / name.removeprefix("XDG_").removesuffix("_HOME").lower()
            directory.mkdir()
            os.environ[name] = str(directory)
        for name in HOME_OVERRIDES:
            os.environ.pop(name, None)

    def protected(self, path: str) -> bool:
        if any(_inside(path, root) for root in self.allowed):
            return False
        return any(_inside(path, home) for home in self.real_home)

    def written_paths(self, event: str, args: tuple[object, ...]) -> list[object]:
        if event == "open":
            mode, flags = args[1], args[2]
            writes = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                isinstance(flags, int) and flags & WRITE_FLAGS
            )
            return [args[0]] if writes else []
        return [args[index] for index in PATH_EVENTS.get(event, ()) if index < len(args)]

    def audit(self, event: str, args: tuple[object, ...]) -> None:
        if event != "open" and event not in PATH_EVENTS:
            return
        for raw in self.written_paths(event, args):
            path = _absolute(raw)
            if path is not None and self.protected(path):
                message = (
                    f"{self.item or 'session'}: {RULE}: {event} would change {path} under the "
                    "real home; write under tmp_path or the redirected HOME instead"
                )
                self.violations.append(message)
                self.current.append(message)
                raise UserDataGuardError(message)

    @wrapper_hook
    def pytest_runtest_protocol(self, item: pytest.Item) -> Iterator[None]:
        self.item = item.nodeid
        try:
            yield
        finally:
            self.item = ""

    @wrapper_hook
    def pytest_runtest_makereport(
        self, item: pytest.Item, call: pytest.CallInfo[None]
    ) -> Generator[None, Result[pytest.TestReport], None]:
        outcome = yield
        report = outcome.get_result()
        if self.current:
            report.outcome = "failed"
            report.longrepr = "\n".join(self.current)
            self.current = []

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        if self.violations:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def pytest_terminal_summary(self, terminalreporter: pytest.TerminalReporter) -> None:
        for message in self.violations:
            terminalreporter.write_line(message)

    def pytest_unconfigure(self) -> None:
        shutil.rmtree(self.home)


def _interpreter_roots() -> tuple[Path, ...]:
    return tuple(Path(prefix) for prefix in {sys.prefix, sys.base_prefix, sys.exec_prefix})


GUARD = UserDataGuard(
    Path(pwd.getpwuid(os.getuid()).pw_dir),
    (Path(__file__).resolve().parents[2], Path(tempfile.gettempdir()), *_interpreter_roots()),
)
GUARD.redirect()
sys.addaudithook(GUARD.audit)

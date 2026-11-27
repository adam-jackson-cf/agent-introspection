import sys
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager

import pytest
from pluggy import Result

RULE = "TEST.t1-isolation"
T2_MARKERS = ("boundary",)
PROCESS_EVENTS = frozenset({
    "os.exec",
    "os.fork",
    "os.forkpty",
    "os.posix_spawn",
    "os.spawn",
    "os.system",
    "pty.spawn",
    "subprocess.Popen",
})
NETWORK_EVENTS = frozenset({"socket.bind", "socket.connect", "socket.sendmsg", "socket.sendto"})


def wrapper_hook[Hook: Callable[..., object]](function: Hook) -> Hook:
    marked: Hook = pytest.hookimpl(hookwrapper=True)(function)
    return marked


class TierIsolationError(RuntimeError):
    pass


def _describe(event: str, args: tuple[object, ...]) -> str:
    if event == "subprocess.Popen" and len(args) > 1:
        return f"{event} {args[1]!r}"
    if event in NETWORK_EVENTS and len(args) > 1:
        return f"{event} {args[1]!r}"
    return event


class TierGuard:
    def __init__(self) -> None:
        self.item = ""
        self.phase = ""
        self.current: list[str] = []

    def audit(self, event: str, args: tuple[object, ...]) -> None:
        if not self.item or (event not in PROCESS_EVENTS and event not in NETWORK_EVENTS):
            return
        kind = "started a process" if event in PROCESS_EVENTS else "opened a network connection"
        message = (
            f"{self.item}: {RULE}: T1 {self.phase} {kind} ({_describe(event, args)}); "
            "fake the owned adapter seam, or move the test to a boundary (T2) file"
        )
        self.current.append(message)
        raise TierIsolationError(message)

    @contextmanager
    def _guarded(self, item: pytest.Item, phase: str) -> Iterator[None]:
        if any(item.get_closest_marker(name) for name in T2_MARKERS):
            yield
            return
        self.item, self.phase = item.nodeid, phase
        try:
            yield
        finally:
            self.item = self.phase = ""

    @wrapper_hook
    def pytest_runtest_setup(self, item: pytest.Item) -> Iterator[None]:
        with self._guarded(item, "setup"):
            yield

    @wrapper_hook
    def pytest_runtest_call(self, item: pytest.Item) -> Iterator[None]:
        with self._guarded(item, "call"):
            yield

    @wrapper_hook
    def pytest_runtest_teardown(self, item: pytest.Item) -> Iterator[None]:
        with self._guarded(item, "teardown"):
            yield

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


GUARD = TierGuard()
sys.addaudithook(GUARD.audit)

import sqlite3
import subprocess
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack, closing
from pathlib import Path

import pytest

from agent_introspection import doctor_harness, hook_install, hooks
from agent_introspection.workflow import connect_workflow
from tests.support.otlp_collector import Collector

type OpenWorkflow = Callable[..., sqlite3.Connection]


@pytest.fixture(autouse=True)
def isolated_home(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point HOME at an empty directory, so no test reads or writes the real home.

    `harness configure` installs hooks under the home; a test that needs a populated
    home sets HOME again itself.
    """
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    return home


class FakeOmp:
    """Stands in for the `omp` binary: `plugin link` and `plugin uninstall` act on HOME."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(command[1:]))
        link = Path.home() / doctor_harness.OMP_PLUGIN_LINK
        if list(command[1:3]) == ["plugin", "link"]:
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(command[3])
        elif link.is_symlink():
            link.unlink()
        return subprocess.CompletedProcess(list(command), 0, "", "")


@pytest.fixture(autouse=True)
def fake_omp(monkeypatch: pytest.MonkeyPatch) -> FakeOmp:
    """No test runs the real omp: hook installs find and run this stand-in."""
    omp = FakeOmp()
    monkeypatch.setattr(hook_install, "_which", lambda name: f"/fake/bin/{name}")
    monkeypatch.setattr(hook_install, "_run_omp", omp)
    return omp


@pytest.fixture
def open_workflow() -> Iterator[OpenWorkflow]:
    """Open workflow stores that the test closes when it ends."""
    with ExitStack() as stack:

        def open_store(path: Path | str = ":memory:", **options: int) -> sqlite3.Connection:
            return stack.enter_context(closing(connect_workflow(path, **options)))

        yield open_store


@pytest.fixture
def collector() -> Iterator[Collector]:
    """A local OTLP/HTTP logs collector, shut down when the test ends."""
    server = Collector()
    yield server
    server.close()


@pytest.fixture
def inbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated record spool and hook log, with no OTEL endpoint in the environment."""
    directory = tmp_path / "data" / "hook-inbox"
    monkeypatch.setattr(hooks, "INBOX", directory)
    monkeypatch.setattr(hooks, "LOG", tmp_path / "data" / "hooks.log")
    for name in (
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
        "OTEL_EXPORTER_OTLP_PROTOCOL",
        "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL",
    ):
        monkeypatch.delenv(name, raising=False)
    return directory

#!/usr/bin/env python3
"""Install canonical Codex app-server attribution for future Desktop launches."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Final

_LABEL: Final = "com.adamjackson.agent-introspection-codex-app-server"
_MANAGED_RUNTIME: Final = Path(".local/lib/agent-introspection/session-context-runtime-v1")
_REAL_CODEX: Final = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
_PROXY: Final = "proxy.py"
_REAL_CLI_CONFIG: Final = "codex-app-server-real-cli"


class InstallError(RuntimeError):
    """The managed app-server integration could not be installed safely."""


def _require_executable(path: Path) -> Path:
    candidate = path.resolve()
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise InstallError("the bundled Codex executable is unavailable")
    return candidate


def _atomic_install(source: Path, destination: Path, mode: int) -> None:
    if not source.is_file():
        raise InstallError(f"required source file is unavailable: {source.name}")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}-", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with source.open("rb") as input_file, os.fdopen(descriptor, "wb") as output_file:
            shutil.copyfileobj(input_file, output_file)
            output_file.flush()
            os.fsync(output_file.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write(destination: Path, content: bytes, mode: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}-", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output_file:
            output_file.write(content)
            output_file.flush()
            os.fsync(output_file.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _launch_agent_payload(proxy: Path) -> dict[str, object]:
    return {
        "Label": _LABEL,
        "ProcessType": "Background",
        "ProgramArguments": [
            "/bin/launchctl",
            "setenv",
            "CODEX_CLI_PATH",
            str(proxy),
        ],
        "RunAtLoad": True,
    }


def _replace_launch_agent(destination: Path, launchctl: Path, proxy: Path) -> None:
    domain = f"gui/{os.getuid()}"
    service = f"{domain}/{_LABEL}"
    probe = subprocess.run(
        [str(launchctl), "print", service],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if probe.returncode == 0:
        subprocess.run([str(launchctl), "bootout", service], check=True)
    subprocess.run([str(launchctl), "bootstrap", domain, str(destination)], check=True)
    deadline = time.monotonic() + 5
    while True:
        current = subprocess.run(
            [str(launchctl), "getenv", "CODEX_CLI_PATH"],
            check=False,
            capture_output=True,
            text=True,
        )
        if current.returncode == 0 and current.stdout.strip() == str(proxy):
            return
        if time.monotonic() >= deadline:
            raise InstallError("the user launch environment did not accept CODEX_CLI_PATH")
        time.sleep(0.05)


def install(
    *,
    home: Path,
    real_codex: Path,
    launchctl: Path = Path("/bin/launchctl"),
    activate: bool = True,
) -> tuple[Path, Path]:
    source_dir = Path(__file__).resolve().parent
    scripts_dir = source_dir.parent.parent
    managed_dir = home / _MANAGED_RUNTIME
    managed_adapter_dir = managed_dir / "adapters/codex-app-server"
    proxy = managed_adapter_dir / _PROXY
    real_cli = _require_executable(real_codex)
    if real_cli == Path(__file__).resolve() or real_cli == proxy.resolve():
        raise InstallError("the real Codex executable cannot resolve to the proxy")

    _atomic_install(
        scripts_dir / "session-context-runtime.sh",
        managed_dir / "session-context-runtime.sh",
        0o755,
    )
    _atomic_install(
        source_dir / "adapter.sh",
        managed_adapter_dir / "adapter.sh",
        0o755,
    )
    _atomic_install(source_dir / _PROXY, proxy, 0o755)
    _atomic_write(managed_adapter_dir / _REAL_CLI_CONFIG, f"{real_cli}\n".encode(), 0o444)

    launch_agent = home / "Library/LaunchAgents" / f"{_LABEL}.plist"
    payload = plistlib.dumps(_launch_agent_payload(proxy), sort_keys=True)
    _atomic_write(launch_agent, payload, 0o644)
    if activate:
        _replace_launch_agent(launch_agent, launchctl, proxy)
    return proxy, launch_agent


def main() -> int:
    if sys.platform != "darwin":
        raise InstallError("Codex Desktop attribution installation requires macOS")
    proxy, launch_agent = install(home=Path.home(), real_codex=_REAL_CODEX)
    print(f"installed proxy: {proxy}")
    print(f"installed launch agent: {launch_agent}")
    print("Codex Desktop was not restarted; restart it manually to use the proxy.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (InstallError, OSError, subprocess.CalledProcessError) as error:
        print(f"install-codex-app-server: {error}", file=sys.stderr)
        raise SystemExit(os.EX_CONFIG) from error

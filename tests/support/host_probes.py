from __future__ import annotations

import contextlib
import subprocess
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import pytest

from agent_introspection import doctor, doctor_harness, profiles, schedule
from tests.support.http_response import CannedResponse


def verified_versions() -> dict[str, str]:
    return {profile.binary: profile.verified_versions[0] for profile in profiles.load_profiles()}


def fake_version_probe(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    versions = verified_versions()
    ran: list[list[str]] = []

    def run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        ran.append(list(argv))
        printed = versions.get(Path(argv[0]).name, "")
        return subprocess.CompletedProcess(list(argv), 0, f"{printed}\n", "")

    monkeypatch.setattr(doctor_harness, "_run_version", run)
    return ran


def fake_network(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, bytes]]:
    posted: list[tuple[str, bytes]] = []

    def urlopen(request: urllib.request.Request) -> CannedResponse:
        posted.append((request.full_url, cast(bytes, request.data)))
        return CannedResponse(b"{}")

    monkeypatch.setattr(doctor, "_connect", lambda _host, _port: contextlib.nullcontext())
    monkeypatch.setattr(doctor, "_urlopen", urlopen)
    monkeypatch.setattr(doctor, "_open_local", lambda _url: CannedResponse(b"Ok.\n"))
    return posted


def fake_systemd(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def systemctl(*args: str) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        printed = {"is-enabled": "enabled\n", "is-active": "active\n"}.get(args[0], "")
        return subprocess.CompletedProcess(("systemctl", "--user", *args), 0, printed, "")

    monkeypatch.setattr(schedule, "platform_name", lambda: "linux")
    monkeypatch.setattr(schedule, "systemctl", systemctl)
    monkeypatch.setattr(schedule, "_linger_file_exists", lambda _user: True)
    return calls

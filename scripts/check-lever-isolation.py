#!/usr/bin/env python3
from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from lib.command_runner import isolated_environment, run_command
from lib.proofs_settings import PROOFS_FILE, SettingsError, load_proofs, string, strings, table

RULE = "SURFACE.lever-isolation"
PS_TIMEOUT_SECONDS = 20
CONNECT_TIMEOUT_SECONDS = 1.0


@dataclass(frozen=True)
class Lever:
    command: list[str]
    data_arg: str
    evidence_arg: str
    marker: str
    ports_file: str
    timeout_seconds: float
    failure_environment: dict[str, str]


def load_lever(repo: Path) -> Lever:
    surface = table(load_proofs(repo), "surface")
    timeout = surface.get("lever_timeout_s")
    failure = surface.get("doctor_failure_env")
    if isinstance(timeout, bool) or not isinstance(timeout, int | float) or timeout <= 0:
        raise SettingsError("[surface] lever_timeout_s must be a positive number")
    if (
        not isinstance(failure, dict)
        or not failure
        or not all(isinstance(v, str) for v in failure.values())
    ):
        raise SettingsError("[surface] doctor_failure_env must be a nonempty table of strings")
    return Lever(
        strings(surface, "lever_command", "surface"),
        string(surface, "data_dir_arg", "surface"),
        string(surface, "evidence_dir_arg", "surface"),
        string(surface, "doctor_marker", "surface"),
        string(surface, "evidence_ports_file", "surface"),
        float(timeout),
        {str(key): value for key, value in failure.items()},
    )


def _launch(repo: Path, lever: Lever, work: Path, extra: dict[str, str]) -> tuple[int, str]:
    argv = [
        *lever.command,
        lever.data_arg,
        str(work / "data"),
        lever.evidence_arg,
        str(work / "evidence"),
    ]
    try:
        completed = run_command(argv, repo, lever.timeout_seconds, isolated_environment(extra))
    except subprocess.TimeoutExpired:
        return -1, f"lever ran past {lever.timeout_seconds:g}s"
    return completed.returncode, completed.stdout + completed.stderr


def _leftover_processes(repo: Path, data: Path) -> list[str]:
    listing = run_command(["ps", "-A", "-o", "pid=,command="], repo, PS_TIMEOUT_SECONDS)
    return [line.strip() for line in listing.stdout.splitlines() if str(data) in line]


def _listening(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(CONNECT_TIMEOUT_SECONDS)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _run_problems(repo: Path, lever: Lever, work: Path) -> list[str]:
    code, output = _launch(repo, lever, work, {})
    evidence, data = work / "evidence", work / "data"
    problems: list[str] = []
    if code != 0:
        kind = (
            "doctor reported blocked infrastructure" if lever.marker in output else "lever failed"
        )
        problems.append(f"{lever.command[1]}: {RULE}: {kind} (exit {code}); see {evidence}")
    if not evidence.is_dir() or not any(evidence.iterdir()):
        problems.append(
            f"{lever.command[1]}: {RULE}: evidence directory is empty after cleanup; keep it"
        )
    if data.exists():
        problems.append(
            f"{lever.command[1]}: {RULE}: data directory {data} survives cleanup; delete it"
        )
    problems.extend(
        f"{lever.command[1]}: {RULE}: process left running after cleanup: {line}; "
        "stop it in cleanup"
        for line in _leftover_processes(repo, data)
    )
    ports = evidence / lever.ports_file
    if code == 0 and not ports.is_file():
        problems.append(
            f"{lever.command[1]}: {RULE}: evidence lacks {lever.ports_file}; record every port used"
        )
    elif ports.is_file():
        problems.extend(
            f"{lever.command[1]}: {RULE}: port {port} still listens after cleanup; stop its process"
            for port in (int(item) for item in ports.read_text(encoding="utf-8").split())
            if _listening(port)
        )
    return problems


def _doctor_problems(repo: Path, lever: Lever, work: Path) -> list[str]:
    code, output = _launch(repo, lever, work, lever.failure_environment)
    if code != 0 and lever.marker in output:
        return []
    return [
        f"{lever.command[1]}: {RULE}: a failing doctor did not print {lever.marker!r} and exit "
        f"nonzero (exit {code}); print the marker when the lever's infrastructure is not ready"
    ]


def _print_evidence(evidence: Path) -> None:
    for log in sorted(evidence.glob("*.log")):
        print(f"== {log}", file=sys.stderr)
        print(log.read_text(encoding="utf-8", errors="replace"), file=sys.stderr)


def check(repo: Path) -> list[str]:
    lever = load_lever(repo)
    root = Path(tempfile.mkdtemp(prefix="lever-isolation-"))
    print(f"check-lever-isolation: evidence under {root}", file=sys.stderr)
    (root / "doctor").mkdir()
    (root / "run").mkdir()
    problems = [
        *_doctor_problems(repo, lever, root / "doctor"),
        *_run_problems(repo, lever, root / "run"),
    ]
    if problems:
        _print_evidence(root / "run" / "evidence")
    return problems


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="check-lever-isolation.py")
    parser.add_argument("--repo", type=Path, default=Path())
    arguments = parser.parse_args(argv)
    try:
        problems = check(arguments.repo)
    except SettingsError as error:
        print(f"{PROOFS_FILE}: {RULE}: {error}; fix [surface]", file=sys.stderr)
        return 1
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import shlex
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path

import yaml

_PARITY = "GATE.parity"
_SELF_CHECK = "GATE.self-check"
_ARGUMENT = re.compile(r"^[A-Za-z0-9_./=-]+$")
_HOOK_MODES = re.compile(r"^hook_modes=\((?P<modes>[^)]*)\)$", re.MULTILINE)
_CODEOWNERS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")


@dataclass
class Gate:
    root: Path
    proofs: dict[str, object]
    problems: list[str] = field(default_factory=list)
    ci_modes: set[str] = field(default_factory=set)

    def fail(self, path: str, rule: str, problem: str, fix: str) -> None:
        self.problems.append(f"{(self.root / path).as_posix()}: {rule}: {problem}; {fix}")


def _yaml(path: Path) -> dict[str, object]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a YAML mapping")
    return {str(key): value for key, value in data.items()}


def _claims(gate: Gate) -> list[dict[str, object]]:
    claims = gate.proofs.get("claim", [])
    return (
        [claim for claim in claims if isinstance(claim, dict)] if isinstance(claims, list) else []
    )


def _points(claim: dict[str, object]) -> list[dict[str, str]]:
    points = claim.get("enforced_by", [])
    if not isinstance(points, list):
        return []
    return [
        {str(key): str(value) for key, value in point.items()}
        for point in points
        if isinstance(point, dict)
    ]


def _all_points(gate: Gate) -> list[dict[str, str]]:
    return [point for claim in _claims(gate) for point in _points(claim)]


def _steps(job: dict[str, object]) -> list[dict[str, object]]:
    steps = job.get("steps", [])
    return [step for step in steps if isinstance(step, dict)] if isinstance(steps, list) else []


def _invocation(command: str, entrypoint: str) -> list[str] | None:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if tokens[:1] == ["bash"]:
        tokens = tokens[1:]
    if not tokens or tokens[0].removeprefix("./") != entrypoint:
        return None
    if not all(_ARGUMENT.match(token) for token in tokens[1:]):
        return None
    return [entrypoint, *tokens[1:]]


def _check_entrypoints(gate: Gate) -> str | None:
    entrypoints: set[str] = set()
    for claim in _claims(gate):
        found = {
            point["ref"].split()[0] for point in _points(claim) if point.get("ref", "").split()
        }
        if len(found) > 1:
            gate.fail(
                "proofs.toml",
                _PARITY,
                f"claim {claim.get('id')} points invoke {', '.join(sorted(found))}",
                "make every point of the claim run the same entrypoint",
            )
        entrypoints |= found
    if len(entrypoints) != 1:
        gate.fail(
            "proofs.toml",
            _PARITY,
            f"claims name {len(entrypoints)} entrypoints",
            "route every hook and CI point through one entrypoint script",
        )
        return None
    return entrypoints.pop()


def _check_hook_file(gate: Gate, path: str, entrypoint: str, refs: set[str]) -> None:
    config = _yaml(gate.root / path)
    repos = config.get("repos", [])
    hooks = [
        hook
        for repo in (repos if isinstance(repos, list) else [])
        if isinstance(repo, dict)
        for hook in repo.get("hooks", [])
        if isinstance(hook, dict)
    ]
    for hook in hooks:
        command = _invocation(str(hook.get("entry", "")), entrypoint)
        hook_id = hook.get("id")
        if command is None or shlex.join(command) not in refs:
            gate.fail(
                path,
                _PARITY,
                f"hook {hook_id} runs '{hook.get('entry')}' outside {' or '.join(sorted(refs))}",
                f"move it into {entrypoint} so CI runs the same check",
            )
            continue
        if hook.get("pass_filenames") is not False or hook.get("always_run") is not True:
            gate.fail(
                path,
                _PARITY,
                f"hook {hook_id} does not run on every commit over the whole repo",
                "set pass_filenames: false and always_run: true",
            )
        if any(key in hook for key in ("files", "exclude", "types", "types_or")):
            gate.fail(path, _PARITY, f"hook {hook_id} filters files", "remove its file filters")


def _job_invocation(gate: Gate, path: str, name: str, job: object, entrypoint: str) -> list[str]:
    steps = job.get("steps", []) if isinstance(job, dict) else []
    runs = [str(step["run"]) for step in steps if isinstance(step, dict) and "run" in step]
    commands = [_invocation(run.strip(), entrypoint) for run in runs]
    if len(runs) != 1 or commands[0] is None:
        gate.fail(
            path,
            _PARITY,
            f"job {name} does not run exactly one unmasked {entrypoint} step",
            f"make its only run step 'bash {entrypoint} <mode>'",
        )
        return []
    return commands[0]


def _aggregate_needs(gate: Gate, path: str, name: str, job: dict[str, object]) -> list[str]:
    needs = job.get("needs")
    needs = [needs] if isinstance(needs, str) else needs
    if not isinstance(needs, list) or not needs:
        return []
    if "always()" not in str(job.get("if", "")):
        gate.fail(path, _PARITY, f"aggregate {name} skips after a failed tier", "add if: always()")
    script = "\n".join(str(step.get("run", "")) for step in _steps(job))
    for need in needs:
        check = f'test "${{{{ needs.{need}.result }}}}" = success'
        if check not in script:
            gate.fail(
                path,
                _PARITY,
                f"aggregate {name} does not fail when {need} fails, is cancelled or skipped",
                f"add the step line {check}",
            )
    return [str(need) for need in needs]


def _status_commands(gate: Gate, path: str, status: str, entrypoint: str) -> list[list[str]]:
    jobs = _yaml(gate.root / path).get("jobs", {})
    job = jobs.get(status) if isinstance(jobs, dict) else None
    if not isinstance(job, dict) or not isinstance(jobs, dict):
        gate.fail(path, _PARITY, f"no job posts status {status}", f"add a job named {status}")
        return []
    needs = _aggregate_needs(gate, path, status, job)
    names = needs or [status]
    commands = [_job_invocation(gate, path, name, jobs.get(name), entrypoint) for name in names]
    return [command for command in commands if command]


def _check_ci_points(gate: Gate, entrypoint: str) -> None:
    for point in _all_points(gate):
        if point.get("point") != "ci":
            continue
        commands = _status_commands(gate, point["file"], point["status"], entrypoint)
        gate.ci_modes |= {command[1] for command in commands if len(command) > 1}
        if point["ref"].split() not in commands:
            gate.fail(
                point["file"],
                _PARITY,
                f"status {point['status']} does not run '{point['ref']}'",
                f"run 'bash {point['ref']}' in a job under {point['status']}",
            )


def _self_check_points(gate: Gate) -> list[dict[str, str]]:
    return [
        point
        for claim in _claims(gate)
        if isinstance(claim.get("rules"), list)
        and any(fnmatchcase(_SELF_CHECK, str(rule)) for rule in claim["rules"])
        for point in _points(claim)
        if point.get("point") == "ci"
    ]


def _own_job(gate: Gate, point: dict[str, str], entrypoint: str) -> bool:
    jobs = _yaml(gate.root / point["file"]).get("jobs", {})
    job = jobs.get(point["status"]) if isinstance(jobs, dict) else None
    if not isinstance(job, dict) or job.get("needs"):
        return False
    runs = [str(step["run"]).strip() for step in _steps(job) if "run" in step]
    return [_invocation(run, entrypoint) for run in runs] == [point["ref"].split()]


def _check_validator(gate: Gate, entrypoint: str) -> None:
    if not any(point.get("point") == "ci" for point in _all_points(gate)):
        return
    points = _self_check_points(gate)
    if any(_own_job(gate, point, entrypoint) for point in points):
        return
    gate.fail(
        points[0]["file"] if points else "proofs.toml",
        _SELF_CHECK,
        "no ci point of a GATE.self-check claim runs the validator as its own job",
        f"add a ci point whose status job runs only 'bash {entrypoint} proofs'",
    )


def _check_hook_modes(gate: Gate, entrypoint: str) -> None:
    match = _HOOK_MODES.search((gate.root / entrypoint).read_text(encoding="utf-8"))
    if match is None:
        gate.fail(entrypoint, _PARITY, "no hook_modes array", "declare hook_modes=(<mode> ...)")
        return
    for mode in match.group("modes").split():
        if mode not in gate.ci_modes:
            gate.fail(
                entrypoint,
                _PARITY,
                f"the hook runs {mode} but no CI job under a claimed status runs it",
                f"add a job running 'bash {entrypoint} {mode}' under the gate status",
            )


def _owned(patterns: list[tuple[str, list[str]]], path: str) -> bool:
    owners: list[str] = []
    for pattern, names in patterns:
        anchored = pattern.lstrip("/")
        if pattern.endswith("/"):
            matched = path.startswith(anchored)
        elif "/" in pattern.rstrip("/"):
            matched = fnmatchcase(path, anchored) or path.startswith(anchored.rstrip("*") + "/")
        else:
            matched = any(fnmatchcase(part, anchored) for part in path.split("/"))
        if matched:
            owners = names
    return bool(owners)


def _check_owners(gate: Gate, entrypoint: str) -> None:
    location = next((name for name in _CODEOWNERS if (gate.root / name).is_file()), None)
    if location is None:
        gate.fail(".github/CODEOWNERS", _SELF_CHECK, "no CODEOWNERS file", "add .github/CODEOWNERS")
        return
    patterns = [
        (line.split()[0], line.split()[1:])
        for line in (gate.root / location).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    protected = {"proofs.toml", entrypoint} | {point["file"] for point in _all_points(gate)}
    for claim in _claims(gate):
        rules, controls = claim.get("rules", []), claim.get("control", [])
        if not isinstance(rules, list) or not isinstance(controls, list):
            continue
        if any(fnmatchcase(_SELF_CHECK, str(rule)) for rule in rules):
            protected |= {str(item) for item in controls}
    for path in sorted(protected):
        if not _owned(patterns, path):
            gate.fail(location, _SELF_CHECK, f"{path} has no code owner", f"add '/{path} @<owner>'")


def check(root: Path) -> list[str]:
    try:
        proofs = tomllib.loads((root / "proofs.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        return [f"proofs.toml: {_PARITY}: cannot read: {error}; restore proofs.toml"]
    gate = Gate(root, proofs)
    entrypoint = _check_entrypoints(gate)
    if entrypoint is None:
        return gate.problems
    try:
        points = _all_points(gate)
        for path in sorted({point["file"] for point in points if point["point"] == "hook"}):
            refs = {point["ref"] for point in points if point["file"] == path}
            _check_hook_file(gate, path, entrypoint, refs)
        _check_ci_points(gate, entrypoint)
        _check_validator(gate, entrypoint)
        _check_hook_modes(gate, entrypoint)
        _check_owners(gate, entrypoint)
    except (OSError, ValueError, yaml.YAMLError) as error:
        gate.fail("proofs.toml", _PARITY, f"cannot read a point file: {error}", "fix the file")
    return gate.problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail when enforcement points drift apart.")
    parser.add_argument("root", type=Path, nargs="?", default=Path())
    args = parser.parse_args(argv)
    problems = sorted(set(check(args.root)))
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"check-quality-gate-parity: {len(problems)} violations", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())

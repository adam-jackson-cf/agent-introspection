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

from lib import filesystem

_PARITY = "GATE.parity"
_SELF_CHECK = "GATE.self-check"
_ARGUMENT = re.compile(r"^[A-Za-z0-9_./=-]+$")
_HOOK_MODES = re.compile(r"^hook_modes=\((?P<modes>[^)]*)\)$", re.MULTILINE)
_BRANCH = re.compile(r"^\s*(?P<labels>[A-Za-z0-9_-]+(?:\s*\|\s*[A-Za-z0-9_-]+)*)\)\s*$")
_SHEBANGS = ("#!/bin/sh", "#!/usr/bin/env sh")
_SHELLS = ("bash", "sh")
_WORKFLOWS = ".github/workflows"
_CODEOWNERS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")


@dataclass
class EntryScript:
    path: str
    branches: dict[str, list[list[str]]]

    def runs(self, branch: str) -> list[str] | None:
        commands = self.branches.get(branch)
        return commands[-1] if commands else None


@dataclass
class Gate:
    root: Path
    proofs: dict[str, object]
    problems: list[str] = field(default_factory=list)
    ci_modes: set[str] = field(default_factory=set)

    def fail(self, path: str, rule: str, problem: str, fix: str) -> None:
        self.problems.append(f"{(self.root / path).as_posix()}: {rule}: {problem}; {fix}")


def _yaml(path: Path) -> dict[str, object]:
    data = yaml.safe_load(filesystem.read_text(path)) or {}
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


def _covers_self_check(claim: dict[str, object]) -> bool:
    rules = claim.get("rules")
    return isinstance(rules, list) and any(fnmatchcase(_SELF_CHECK, str(rule)) for rule in rules)


def _steps(job: object) -> list[dict[str, object]]:
    steps = job.get("steps", []) if isinstance(job, dict) else []
    return [step for step in steps if isinstance(step, dict)] if isinstance(steps, list) else []


def _tokens(command: str) -> list[str] | None:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if len(tokens) > 1 and tokens[0] in _SHELLS:
        tokens = tokens[1:]
    if not tokens or not all(_ARGUMENT.match(token) for token in tokens):
        return None
    return [tokens[0].removeprefix("./"), *tokens[1:]]


def _invocation(command: str, entrypoint: str) -> list[str] | None:
    tokens = _tokens(command)
    return tokens if tokens is not None and tokens[0] == entrypoint else None


def _branch_commands(gate: Gate, path: str, lines: list[str]) -> list[list[str]] | None:
    commands: list[list[str]] = []
    for line in lines:
        tokens = _tokens(line.removeprefix("exec ").strip())
        if tokens is None:
            gate.fail(
                path,
                _PARITY,
                f"branch line '{line}' is masked or not a plain command",
                "run one plain command per line with no shell operators",
            )
            return None
        commands.append(tokens)
    if not lines or not lines[-1].startswith("exec "):
        gate.fail(
            path,
            _PARITY,
            "a branch does not end by exec-ing its command",
            "end every case branch with 'exec <command>'",
        )
        return None
    return commands


def _entry_script(gate: Gate, path: str) -> EntryScript | None:
    text = filesystem.read_text(gate.root / path)
    lines = [line.strip() for line in text.splitlines()]
    if not lines or lines[0] not in _SHEBANGS or "set -eu" not in lines:
        gate.fail(
            path,
            _PARITY,
            "the CI entry script is not a POSIX sh script with set -eu",
            "start it with #!/bin/sh and set -eu",
        )
        return None
    branches: dict[str, list[list[str]]] = {}
    labels: list[str] = []
    body: list[str] = []
    for line in lines:
        match = _BRANCH.match(line)
        if match is not None:
            labels, body = [label.strip() for label in match["labels"].split("|")], []
        elif line == ";;" and labels:
            commands = _branch_commands(gate, path, body)
            if commands is not None:
                branches.update(dict.fromkeys(labels, commands))
            labels = []
        elif labels and line:
            body.append(line)
    return EntryScript(path, branches)


def _hooks(config: dict[str, object]) -> list[dict[str, object]]:
    repos = config.get("repos", [])
    return [
        hook
        for repo in (repos if isinstance(repos, list) else [])
        if isinstance(repo, dict)
        for hook in repo.get("hooks", [])
        if isinstance(hook, dict)
    ]


def _check_hook_file(gate: Gate, path: str, entrypoint: str, refs: set[str]) -> None:
    for hook in _hooks(_yaml(gate.root / path)):
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


def _workflows(gate: Gate) -> dict[str, dict[str, object]]:
    folder = gate.root / _WORKFLOWS
    if not filesystem.is_dir(folder):
        return {}
    files = sorted(
        path for pattern in ("*.yml", "*.yaml") for path in filesystem.glob(folder, pattern)
    )
    workflows: dict[str, dict[str, object]] = {}
    for path in files:
        jobs = _yaml(path).get("jobs", {})
        if isinstance(jobs, dict):
            workflows[path.relative_to(gate.root).as_posix()] = {str(k): v for k, v in jobs.items()}
    return workflows


def _needs(job: object) -> list[str]:
    needs = job.get("needs") if isinstance(job, dict) else None
    needs = [needs] if isinstance(needs, str) else needs
    return [str(need) for need in needs] if isinstance(needs, list) else []


def _job_branch(job: object, entry: EntryScript) -> str | None:
    runs = [str(step["run"]).strip() for step in _steps(job) if "run" in step]
    tokens = _tokens(runs[0]) if len(runs) == 1 else None
    if tokens is None or tokens[0] != entry.path or len(tokens) != 2:
        return None
    return tokens[1] if entry.runs(tokens[1]) is not None else None


def _check_aggregate(gate: Gate, path: str, name: str, job: dict[str, object]) -> None:
    if "always()" not in str(job.get("if", "")):
        gate.fail(path, _PARITY, f"aggregate {name} skips after a failed tier", "add if: always()")
    script = "\n".join(str(step.get("run", "")) for step in _steps(job))
    for need in _needs(job):
        check = f'test "${{{{ needs.{need}.result }}}}" = success'
        if check not in script:
            gate.fail(
                path,
                _PARITY,
                f"aggregate {name} does not fail when {need} fails, is cancelled or skipped",
                f"add the step line {check}",
            )


def _check_jobs(gate: Gate, workflows: dict[str, dict[str, object]], entry: EntryScript) -> None:
    for path, jobs in workflows.items():
        for name, job in jobs.items():
            if _needs(job):
                _check_aggregate(gate, path, name, job if isinstance(job, dict) else {})
            elif _job_branch(job, entry) is None:
                gate.fail(
                    path,
                    _PARITY,
                    f"job {name} does not run exactly one unmasked {entry.path} step",
                    f"make its only run step '{entry.path} <branch>' with a branch {entry.path} "
                    "declares",
                )


def _status_job(
    workflows: dict[str, dict[str, object]], status: str
) -> tuple[dict[str, object], object] | None:
    for jobs in workflows.values():
        if status in jobs:
            return jobs, jobs[status]
    return None


def _status_commands(
    workflows: dict[str, dict[str, object]], status: str, entry: EntryScript
) -> list[list[str]] | None:
    found = _status_job(workflows, status)
    if found is None:
        return None
    jobs, job = found
    names = _needs(job) or [status]
    branches = [_job_branch(jobs.get(name), entry) for name in names]
    return [command for branch in branches if branch for command in [entry.runs(branch)] if command]


def _entry_path(gate: Gate) -> str | None:
    files = sorted({point["file"] for point in _all_points(gate) if point.get("point") == "ci"})
    if len(files) > 1:
        gate.fail(
            "proofs.toml",
            _PARITY,
            f"ci points name {len(files)} files ({', '.join(files)})",
            "name the one CI entry script as the file of every ci point",
        )
        return None
    return files[0] if files else None


def _validator_point(point: dict[str, str], claim: dict[str, object], entrypoint: str) -> bool:
    ref = point.get("ref", "").split()
    return (
        point.get("point") == "ci"
        and _covers_self_check(claim)
        and "--root" in ref
        and ref[0] != entrypoint
    )


def _later_command(point: dict[str, str], entry: EntryScript | None) -> list[str]:
    tokens = _tokens(point.get("ref", ""))
    if entry is None or tokens is None or tokens[0] != entry.path or len(tokens) != 2:
        return point.get("ref", "").split()
    return entry.runs(tokens[1]) or []


def _effective(point: dict[str, str], entry: EntryScript | None) -> list[str]:
    if point.get("point") == "later":
        return _later_command(point, entry)
    return point.get("ref", "").split()


def _invoked(
    claim: dict[str, object], entry: EntryScript | None, entrypoint: str | None
) -> set[str]:
    commands = [
        _effective(point, entry)
        for point in _points(claim)
        if entrypoint is None or not _validator_point(point, claim, entrypoint)
    ]
    return {command[0] for command in commands if command}


def _check_entrypoints(gate: Gate, entry: EntryScript | None) -> str | None:
    hook_refs = {
        point["ref"].split()[0]
        for point in _all_points(gate)
        if point.get("point") == "hook" and point.get("ref", "").split()
    }
    candidates = hook_refs or {
        command
        for claim in _claims(gate)
        if not _covers_self_check(claim)
        for command in _invoked(claim, entry, None)
    }
    if len(candidates) != 1:
        gate.fail(
            "proofs.toml",
            _PARITY,
            f"claims name {len(candidates)} entrypoints",
            "route every hook and CI point through one entrypoint script",
        )
        return None
    entrypoint = candidates.pop()
    for claim in _claims(gate):
        found = _invoked(claim, entry, entrypoint)
        if found - {entrypoint}:
            gate.fail(
                "proofs.toml",
                _PARITY,
                f"claim {claim.get('id')} points invoke {', '.join(sorted(found | {entrypoint}))}",
                "make every point of the claim run the same entrypoint",
            )
    return entrypoint


def _check_ci_points(
    gate: Gate, workflows: dict[str, dict[str, object]], entry: EntryScript, entrypoint: str
) -> None:
    for point in _all_points(gate):
        if point.get("point") != "ci":
            continue
        commands = _status_commands(workflows, point["status"], entry)
        if commands is None:
            gate.fail(
                _WORKFLOWS,
                _PARITY,
                f"no job posts status {point['status']}",
                f"add a job named {point['status']}",
            )
            continue
        gate.ci_modes |= {
            command[1] for command in commands if len(command) > 1 and command[0] == entrypoint
        }
        if point["ref"].split() not in commands:
            gate.fail(
                entry.path,
                _PARITY,
                f"status {point['status']} does not run '{point['ref']}'",
                f"exec '{point['ref']}' in a {entry.path} branch a job under {point['status']} "
                "calls",
            )


def _check_later_points(gate: Gate, entry: EntryScript | None, entrypoint: str) -> None:
    for point in _all_points(gate):
        if point.get("point") != "later":
            continue
        command = _later_command(point, entry)
        if not command or command[0] != entrypoint:
            gate.fail(
                point["file"],
                _PARITY,
                f"later point '{point['ref']}' does not run {entrypoint}",
                f"call a CI entry script branch that execs {entrypoint}",
            )


def _own_job(
    workflows: dict[str, dict[str, object]], point: dict[str, str], entry: EntryScript
) -> bool:
    found = _status_job(workflows, point["status"])
    if found is None or _needs(found[1]):
        return False
    branch = _job_branch(found[1], entry)
    ref = point["ref"].split()
    return branch is not None and entry.runs(branch) == ref and "--root" in ref


def _check_validator(
    gate: Gate, workflows: dict[str, dict[str, object]], entry: EntryScript | None
) -> None:
    if entry is None:
        return
    points = [
        point
        for claim in _claims(gate)
        if _covers_self_check(claim)
        for point in _points(claim)
        if point.get("point") == "ci"
    ]
    if any(_own_job(workflows, point, entry) for point in points):
        return
    gate.fail(
        entry.path,
        _SELF_CHECK,
        "no ci point of a GATE.self-check claim runs the validator as its own job",
        f"add a ci point whose status job runs only '{entry.path} proofs' and whose branch "
        "execs the shipped validator with --root",
    )


def _check_hook_modes(gate: Gate, entrypoint: str) -> None:
    match = _HOOK_MODES.search(filesystem.read_text(gate.root / entrypoint))
    if match is None:
        gate.fail(entrypoint, _PARITY, "no hook_modes array", "declare hook_modes=(<mode> ...)")
        return
    for mode in match.group("modes").split():
        if mode not in gate.ci_modes:
            gate.fail(
                entrypoint,
                _PARITY,
                f"the hook runs {mode} but no CI job under a claimed status runs it",
                f"add a job whose CI entry script branch execs '{entrypoint} {mode}' under the "
                "gate status",
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


def _check_owners(gate: Gate, entrypoint: str, protected: set[str]) -> None:
    location = next((name for name in _CODEOWNERS if (gate.root / name).is_file()), None)
    if location is None:
        gate.fail(".github/CODEOWNERS", _SELF_CHECK, "no CODEOWNERS file", "add .github/CODEOWNERS")
        return
    patterns = [
        (line.split()[0], line.split()[1:])
        for line in (gate.root / location).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    protected = protected | {"proofs.toml", entrypoint}
    protected |= {point["file"] for point in _all_points(gate)}
    for claim in _claims(gate):
        controls = claim.get("control", [])
        if _covers_self_check(claim) and isinstance(controls, list):
            protected |= {str(item) for item in controls}
    for path in sorted(protected):
        if not _owned(patterns, path):
            gate.fail(location, _SELF_CHECK, f"{path} has no code owner", f"add '/{path} @<owner>'")


def _check(gate: Gate) -> None:
    path = _entry_path(gate)
    entry = _entry_script(gate, path) if path is not None else None
    entrypoint = _check_entrypoints(gate, entry)
    if entrypoint is None:
        return
    points = _all_points(gate)
    for hook_file in sorted({point["file"] for point in points if point["point"] == "hook"}):
        refs = {point["ref"] for point in points if point["file"] == hook_file}
        _check_hook_file(gate, hook_file, entrypoint, refs)
    workflows = _workflows(gate)
    if entry is not None:
        _check_jobs(gate, workflows, entry)
        _check_ci_points(gate, workflows, entry, entrypoint)
    _check_later_points(gate, entry, entrypoint)
    _check_validator(gate, workflows, entry)
    _check_hook_modes(gate, entrypoint)
    _check_owners(gate, entrypoint, set(workflows))


def check(root: Path) -> list[str]:
    try:
        proofs = tomllib.loads(filesystem.read_text(root / "proofs.toml"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        return [f"proofs.toml: {_PARITY}: cannot read: {error}; restore proofs.toml"]
    gate = Gate(root, proofs)
    try:
        _check(gate)
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

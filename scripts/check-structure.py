#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from lib.structure.finding import Finding
from lib.structure.import_rules import build_graph, import_findings, touched_parts
from lib.structure.path_rules import naming_findings, placement_findings, prefix_findings
from lib.structure.rename_rules import residue_findings
from lib.structure.repo_git import GitError, build_scenario, repo_root
from lib.structure.scope import EVENTS, Scope, event_scope, explicit_scope, full_scope
from lib.structure.settings import Settings, SettingsError, load_settings
from lib.structure.typescript_imports import (
    ImportListingError,
    build_graph as build_typescript_graph,
    touched_parts as typescript_parts,
)


def _arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="check-structure",
        description="Judge added and renamed paths against proofs.toml [structure] and [layout].",
    )
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--all", action="store_true", help="judge every tracked path")
    for event in EVENTS:
        scope.add_argument(f"--{event}", dest="event", action="store_const", const=event)
    scope.add_argument("--scenario", type=Path, help="fixture with base/ and head/ trees")
    parser.add_argument("paths", nargs="*", help="explicit paths to judge")
    arguments = parser.parse_args(argv)
    if arguments.paths and (arguments.all or arguments.event or arguments.scenario):
        parser.error("explicit paths cannot be combined with a scope flag")
    return arguments


def _scope(root: Path, settings: Settings, event: str | None, paths: list[str]) -> Scope:
    if event is not None:
        return event_scope(root, settings, event)
    if paths:
        return explicit_scope(root, settings, paths)
    return full_scope(root, settings)


def _typescript_findings(root: Path, settings: Settings, scope: Scope) -> set[Finding]:
    touched = typescript_parts(settings, scope.judged)
    if settings.typescript is None or not touched:
        return set()
    graph = build_typescript_graph(root, settings, scope.head_paths)
    return import_findings(settings.typescript.layering, graph, touched)


def judge(root: Path, event: str | None, paths: list[str]) -> tuple[Scope, list[Finding]]:
    settings = load_settings(root)
    scope = _scope(root, settings, event, paths)
    graph = build_graph(root, settings, scope.head_paths)
    findings = (
        naming_findings(settings, scope)
        | placement_findings(settings, scope)
        | prefix_findings(settings, scope)
        | import_findings(settings.layering, graph, touched_parts(settings, scope.judged))
        | residue_findings(root, scope)
        | _typescript_findings(root, settings, scope)
    )
    kept = sorted(
        finding
        for finding in findings
        if not settings.excepted(finding.rule, finding.path, finding.check)
    )
    return scope, kept


def _report(root: Path, event: str | None, paths: list[str]) -> int:
    scope, findings = judge(root, event, paths)
    for finding in findings:
        print(finding.render())
    print(
        f"check-structure: {len(scope.judged)} paths judged, {len(findings)} findings",
        file=sys.stderr,
    )
    return 1 if findings else 0


def main(argv: list[str]) -> int:
    arguments = _arguments(argv)
    try:
        if arguments.scenario is not None:
            with tempfile.TemporaryDirectory(prefix="check-structure-") as workdir:
                repo = build_scenario(arguments.scenario.resolve(), Path(workdir))
                return _report(repo, "staged", [])
        return _report(repo_root(Path.cwd()), arguments.event, arguments.paths)
    except (GitError, SettingsError, ImportListingError) as error:
        print(f"check-structure: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

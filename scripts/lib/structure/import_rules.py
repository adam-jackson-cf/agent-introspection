from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from lib.structure.finding import Finding
from lib.structure.settings import Settings

DIRECTION = "STRUCT.dependency-direction"
SIBLING = "STRUCT.sibling-isolation"
READERSHIP = "STRUCT.tier-readership"
SINGLE_OWNER = "STRUCT.single-owner-placement"
PACKAGE_ROOT = "__init__"


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    path: str
    line: int


@dataclass(frozen=True)
class ImportGraph:
    part_files: dict[str, tuple[str, ...]]
    edges: tuple[Edge, ...]
    unparsable: tuple[Finding, ...]

    def readers(self, part: str) -> set[str]:
        return {edge.source for edge in self.edges if edge.target == part}


def part_of(settings: Settings, path: str) -> str | None:
    prefix = f"{settings.package_dir}/"
    if not path.startswith(prefix) or not path.endswith(".py"):
        return None
    first, separator, _ = path[len(prefix) :].partition("/")
    return first if separator else first.removesuffix(".py")


def _package_path(settings: Settings, path: str) -> list[str]:
    inner = path[len(settings.package_dir) + 1 :].split("/")
    return [settings.import_package, *inner[:-1]]


def _from_target(settings: Settings, node: ast.ImportFrom, package: list[str]) -> list[str]:
    if node.level:
        base = package[: len(package) - (node.level - 1)]
        full = base + (node.module.split(".") if node.module else [])
    else:
        full = (node.module or "").split(".")
    if not full or full[0] != settings.import_package:
        return []
    if len(full) > 1:
        return [full[1]]
    return [alias.name for alias in node.names]


def _targets(settings: Settings, node: ast.AST, package: list[str]) -> list[str]:
    if isinstance(node, ast.Import):
        names = [alias.name.split(".") for alias in node.names]
        return [
            name[1] if len(name) > 1 else PACKAGE_ROOT
            for name in names
            if name[0] == settings.import_package
        ]
    if isinstance(node, ast.ImportFrom):
        return _from_target(settings, node, package)
    return []


def _file_edges(
    settings: Settings, source: str, path: str, tree: ast.AST, parts: set[str]
) -> Iterator[Edge]:
    package = _package_path(settings, path)
    for node in ast.walk(tree):
        for target in _targets(settings, node, package):
            resolved = target if target in parts else PACKAGE_ROOT
            if resolved != source:
                yield Edge(
                    source=source, target=resolved, path=path, line=getattr(node, "lineno", 0)
                )


def build_graph(root: Path, settings: Settings, head_paths: tuple[str, ...]) -> ImportGraph:
    part_files: dict[str, list[str]] = defaultdict(list)
    for path in head_paths:
        part = part_of(settings, path)
        if part is not None:
            part_files[part].append(path)
    parts = set(part_files)
    edges: list[Edge] = []
    unparsable: list[Finding] = []
    for part, paths in part_files.items():
        for path in paths:
            try:
                tree = ast.parse((root / path).read_text(encoding="utf-8"), filename=path)
            except (OSError, SyntaxError, UnicodeDecodeError) as error:
                unparsable.append(
                    Finding(
                        path,
                        0,
                        DIRECTION,
                        "parse",
                        f"cannot read imports: {error}",
                        "fix the file so its import edges can be judged",
                    )
                )
                continue
            edges.extend(_file_edges(settings, part, path, tree, parts))
    return ImportGraph(
        part_files={part: tuple(sorted(paths)) for part, paths in part_files.items()},
        edges=tuple(edges),
        unparsable=tuple(unparsable),
    )


def _unmapped(settings: Settings, graph: ImportGraph, parts: set[str]) -> Iterator[Finding]:
    tiers = ", ".join(settings.tiers)
    for part in sorted(parts - set(settings.tier_of)):
        if part in graph.part_files:
            yield Finding(
                graph.part_files[part][0],
                0,
                DIRECTION,
                "unmapped",
                f"module part '{part}' has no tier",
                f"have the owner add '{part}' to one [structure].tier_modules list ({tiers})",
            )


def _sibling_fix(settings: Settings, rank: int) -> str:
    if rank:
        return f"move the shared code into '{settings.tiers[rank - 1]}' and import it from both"
    return "merge the two parts, or move the shared code into a lower tier both read"


def _edge_finding(settings: Settings, edge: Edge) -> Iterator[Finding]:
    rank = {tier: index for index, tier in enumerate(settings.tiers)}
    source, target = settings.tier_of[edge.source], settings.tier_of[edge.target]
    if rank[source] < rank[target]:
        yield Finding(
            edge.path,
            edge.line,
            DIRECTION,
            "upward",
            f"{edge.source} ({source}) imports {edge.target} ({target}), a higher tier",
            f"invert the edge: move what {edge.source} needs from {edge.target} into "
            f"'{source}' or lower, or move {edge.source} to '{target}' or higher",
        )
    elif source == target:
        yield Finding(
            edge.path,
            edge.line,
            SIBLING,
            "same-tier",
            f"{edge.source} imports its '{source}' sibling {edge.target}",
            _sibling_fix(settings, rank[source]),
        )


def _reader_findings(settings: Settings, graph: ImportGraph, part: str) -> Iterator[Finding]:
    tier = settings.tier_of[part]
    path = graph.part_files[part][0]
    tier_readers = sorted({
        edge.source
        for edge in graph.edges
        if settings.tier_of.get(edge.target) == tier and settings.tier_of.get(edge.source) != tier
    })
    if len(tier_readers) < 2:
        yield Finding(
            path,
            0,
            READERSHIP,
            "readers",
            f"tier '{tier}' is read by {len(tier_readers)} part(s) ({', '.join(tier_readers)})",
            f"keep the '{tier}' modules flat under their reader until a second part reads the tier",
        )
    readers = sorted(graph.readers(part))
    if len(readers) == 1:
        yield Finding(
            path,
            0,
            SINGLE_OWNER,
            "one-reader",
            f"'{part}' sits in shared tier '{tier}' but only '{readers[0]}' reads it",
            f"move it under its one owner '{readers[0]}' (into that module or its package)",
        )


def touched_parts(settings: Settings, judged: tuple[str, ...]) -> set[str]:
    return {part for part in (part_of(settings, path) for path in judged) if part is not None}


def import_findings(settings: Settings, graph: ImportGraph, touched: set[str]) -> set[Finding]:
    findings: set[Finding] = set()
    judged_edges = [edge for edge in graph.edges if {edge.source, edge.target} & touched]
    endpoints = touched | {part for edge in judged_edges for part in (edge.source, edge.target)}
    findings.update(_unmapped(settings, graph, endpoints))
    findings.update(
        finding
        for edge in judged_edges
        if edge.source in settings.tier_of and edge.target in settings.tier_of
        for finding in _edge_finding(settings, edge)
    )
    top = settings.tiers[-1]
    for part in sorted(touched):
        if settings.tier_of.get(part, top) != top:
            findings.update(_reader_findings(settings, graph, part))
    touched_files = {path for part in touched for path in graph.part_files.get(part, ())}
    findings.update(finding for finding in graph.unparsable if finding.path in touched_files)
    return findings

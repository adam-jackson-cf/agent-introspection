from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from lib.command_runner import run_command
from lib.structure.import_rules import Edge, ImportGraph
from lib.structure.settings import Settings

LISTER = Path(__file__).resolve().parents[2] / "list-typescript-imports.ts"
LIST_TIMEOUT_SECONDS = 120
SOURCE_SUFFIXES = (".ts", ".tsx", ".mts", ".cts")
DECLARATION_SUFFIXES = (".d.ts", ".d.mts", ".d.cts")


class ImportListingError(Exception):
    pass


def part_of(settings: Settings, path: str) -> str | None:
    typescript = settings.typescript
    if (
        typescript is None
        or not path.endswith(SOURCE_SUFFIXES)
        or path.endswith(DECLARATION_SUFFIXES)
    ):
        return None
    if settings.is_fixture(path) or typescript.is_test(path):
        return None
    return path.rsplit(".", 1)[0]


def touched_parts(settings: Settings, judged: tuple[str, ...]) -> set[str]:
    return {part for part in (part_of(settings, path) for path in judged) if part is not None}


def _listed(root: Path, tsconfig: str, files: list[str]) -> list[dict[str, object]]:
    argv = ["bun", str(LISTER), "--root", str(root), "--tsconfig", tsconfig, *files]
    completed = run_command(argv, root, LIST_TIMEOUT_SECONDS)
    if completed.returncode != 0:
        raise ImportListingError(f"typescript import listing failed: {completed.stderr.strip()}")
    edges = json.loads(completed.stdout)["edges"]
    return [edge for edge in edges if isinstance(edge, dict)]


def build_graph(root: Path, settings: Settings, head_paths: tuple[str, ...]) -> ImportGraph:
    if settings.typescript is None:
        return ImportGraph(part_files={}, edges=(), unparsable=())
    part_files: dict[str, list[str]] = defaultdict(list)
    for path in head_paths:
        part = part_of(settings, path)
        if part is not None:
            part_files[part].append(path)
    files = sorted(path for paths in part_files.values() for path in paths)
    edges: list[Edge] = []
    for item in _listed(root, settings.typescript.tsconfig, files) if files else []:
        source = part_of(settings, str(item["file"]))
        resolved = item.get("resolved")
        target = part_of(settings, resolved) if isinstance(resolved, str) else None
        if source is not None and target is not None and target != source:
            edges.append(Edge(source, target, str(item["file"]), int(str(item["line"]))))
    return ImportGraph(
        part_files={part: tuple(sorted(paths)) for part, paths in part_files.items()},
        edges=tuple(edges),
        unparsable=(),
    )

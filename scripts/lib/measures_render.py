"""Render the generated sections of the measure and data-gap docs from the signal registry.

Used by ``scripts/render-measures.py [--check]``, the command that writes or checks the
generated documents.
"""

from __future__ import annotations

import re
from pathlib import Path

from agent_introspection import facts

DOCS = Path(__file__).resolve().parents[2] / "docs"
DOC = DOCS / "dashboard-measure-v3.md"
GAPS = DOCS / "dashboard-data-gaps.md"
_MARK = {"aligned": "✓", "differs": "≈", "not applicable": "—", "not emitted": "✗"}
BEGIN = "<!-- BEGIN GENERATED FROM signal_support.toml -->"
END = "<!-- END GENERATED FROM signal_support.toml -->"


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _code(value: object) -> str:
    return f"`{_cell(value)}`" if value else "—"


def _split_row(line: str) -> list[str]:
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip())[1:-1]]


def _aligned_table(block: list[str]) -> list[str]:
    rows = [_split_row(line) for line in block]
    widths = [max(3, *(len(row[index]) for row in rows)) for index in range(len(rows[0]))]
    rows[1] = ["-" * width for width in widths]
    return [
        "| " + " | ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)) + " |"
        for row in rows
    ]


def _prettier_tables(text: str) -> str:
    """Pad table columns the way Prettier formats Markdown, so regenerated docs stay clean."""
    out: list[str] = []
    block: list[str] = []
    for line in [*text.split("\n"), ""]:
        if line.startswith("|"):
            block.append(line)
            continue
        if block:
            out.extend(_aligned_table(block))
            block = []
        out.append(line)
    return "\n".join(out[:-1])


def _routes(registry: facts.Registry) -> list[str]:
    grouped: dict[str, list[facts.Row]] = {}
    for row in registry.routes:
        grouped.setdefault(str(row["route"]), []).append(row)
    lines = [
        "| Route | Harnesses | Source | Expect | Predicate | Description |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for route, rows in grouped.items():
        first = rows[0]
        harnesses = ", ".join(str(row["harness_label"]) for row in rows)
        lines.append(
            f"| {_code(route)} | {harnesses} | {first['source']} | {first['expect']} | "
            f"{_code(first['match'])} | {_cell(first['description'])} |"
        )
    return lines


def _signal_section(registry: facts.Registry, signal: facts.Row) -> list[str]:
    lines = [
        f"#### {signal['title']} ({_code(signal['signal'])})",
        "",
        f"- **Question:** {signal['question']}",
        f"- **Unit:** {signal['unit']}",
        f"- **Formula:** {signal['formula']}",
        "",
    ]
    support = [row for row in registry.support if row["signal"] == signal["signal"]]
    if not support:
        return [*lines, "Scope: system (not per harness).", ""]
    lines += ["| Harness | Alignment | Route | Note |", "| --- | --- | --- | --- |"]
    lines += [
        f"| {row['harness_label']} | {row['alignment']} | {_code(row['route'])} | "
        f"{_cell(row['note']) or '—'} |"
        for row in support
    ]
    lines.append("")
    return lines


def _signals(registry: facts.Registry) -> list[str]:
    lines: list[str] = []
    views = dict.fromkeys(str(row["view_title"]) for row in registry.signals)
    for view in views:
        lines += [f"### {view}", ""]
        for signal in (row for row in registry.signals if row["view_title"] == view):
            lines += _signal_section(registry, signal)
    return lines


def render() -> str:
    """Return the Markdown catalog: routes, strays, then signals by view."""
    registry = facts.load_registry()
    labels = {str(row["harness"]): str(row["harness_label"]) for row in registry.routes}
    lines = [
        "## 3. Producer routes",
        "",
        "Each route names the rows of one or more harnesses in `introspection.spans`,",
        "`introspection.logs`, or, for source `hooks`, the activity-hook events in",
        "`introspection.hook_rows` that carry a signal. For an `events` route, no rows",
        "while the harness is otherwise active is a valid observation.",
        "",
        *_routes(registry),
        "",
        "### Registered strays",
        "",
        "| Stray | Harness | Source | Predicate | Explanation |",
        "| --- | --- | --- | --- | --- |",
        *(
            f"| {_code(row['stray'])} | {labels[str(row['harness'])]} | {row['source']} | "
            f"{_code(row['match'])} | {_cell(row['reason'])} |"
            for row in registry.strays
        ),
        "",
        "## 4. Signal catalog",
        "",
        *_signals(registry),
    ]
    return _prettier_tables("\n".join(lines).rstrip() + "\n")


def render_gaps() -> str:
    """Return the support matrix and the not-emitted, not-applicable, differs, excluded lists."""
    registry = facts.load_registry()
    harnesses = list(dict.fromkeys(str(row["harness_label"]) for row in registry.support))
    cells = {(str(row["signal"]), str(row["harness_label"])): row for row in registry.support}
    per_harness = [s for s in registry.signals if s["scope"] == "harness"]
    titles = {str(s["signal"]): str(s["title"]) for s in registry.signals}
    counts = dict.fromkeys(_MARK, 0)
    for row in registry.support:
        counts[str(row["alignment"])] += 1
    lines = [
        "## Harness support matrix",
        "",
        f"{len(per_harness)} per-harness signals across {len(harnesses)} harnesses: "
        f"{len(registry.support)} cells, {counts['aligned']} aligned (✓), "
        f"{counts['differs']} reached by a different route (≈), "
        f"{counts['not applicable']} not applicable by construction (—), "
        f"{counts['not emitted']} not emitted (✗).",
        "",
        "Parity is judged per machine, over the harnesses it uses (config `[harnesses] "
        "enabled`, loaded into `introspection.harnesses` by `facts install`; absent means "
        "every harness). A signal is shown when every enabled harness reaches it (✓, ≈, "
        "or —). When an enabled harness does not emit it (✗), the signal is hidden on that "
        "machine, and the Pipeline view lists it with the reason; it is never shown as "
        "zero. A machine using only Claude Code and omp, for example, shows Output "
        "throughput, which Codex cannot produce.",
        "",
        "| View | Signal | " + " | ".join(harnesses) + " |",
        "| --- | --- | " + " | ".join("---" for _ in harnesses) + " |",
    ]
    for signal in per_harness:
        marks = [_MARK[str(cells[str(signal["signal"]), h]["alignment"])] for h in harnesses]
        lines.append(f"| {signal['view_title']} | {signal['title']} | " + " | ".join(marks) + " |")
    lines += [
        "",
        "## Signals a harness does not emit",
        "",
        "Each is hidden on a machine that enables the harness.",
        "",
        "| Signal | Harness | Why |",
        "| --- | --- | --- |",
    ]
    lines += [
        f"| {titles[str(row['signal'])]} | {row['harness_label']} | {_cell(row['note'])} |"
        for row in registry.support
        if row["alignment"] == "not emitted"
    ]
    lines += [
        "",
        "## Signals not applicable to a harness",
        "",
        "| Signal | Harness | Why |",
        "| --- | --- | --- |",
    ]
    lines += [
        f"| {titles[str(row['signal'])]} | {row['harness_label']} | {_cell(row['note'])} |"
        for row in registry.support
        if row["alignment"] == "not applicable"
    ]
    lines += [
        "",
        "## Signals reached by a different route",
        "",
        "These are emitted, but the route differs from the shared definition; the note says how.",
        "",
        "| Signal | Harness | How it differs |",
        "| --- | --- | --- |",
    ]
    lines += [
        f"| {titles[str(row['signal'])]} | {row['harness_label']} | {_cell(row['note'])} |"
        for row in registry.support
        if row["alignment"] == "differs"
    ]
    lines += [
        "",
        "## Excluded signals",
        "",
        "No harness can produce these, natively or through an activity hook, so they are",
        "never shown.",
        "",
        "| Signal | View | Missing for | Reason |",
        "| --- | --- | --- | --- |",
    ]
    views = {str(row["view"]): str(row["view_title"]) for row in registry.signals}
    lines += [
        f"| {row['title']} ({_code(row['signal'])}) | {views[str(row['view'])]} | "
        f"{row['missing']} | {_cell(row['reason'])} |"
        for row in registry.excluded
    ]
    return _prettier_tables("\n".join(lines).rstrip() + "\n")


def updated(text: str, content: str | None = None) -> str:
    """Replace the generated block in ``text`` with a fresh render."""
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{BEGIN}\n\n{render() if content is None else content}\n{END}{tail}"


def rendered() -> dict[Path, str]:
    """Return each generated document with its block refreshed."""
    return {
        DOC: updated(DOC.read_text()),
        GAPS: updated(GAPS.read_text(), render_gaps()),
    }

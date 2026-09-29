"""Render the generated catalog in docs/dashboard-measure-v3.md from the signal registry.

Usage: ``uv run python scripts/render_measures.py [--check]``. With ``--check`` the
script exits non-zero when the committed catalog differs from the registry.
"""

from __future__ import annotations

import sys
from pathlib import Path

from agent_introspection import facts

DOC = Path(__file__).resolve().parents[1] / "docs" / "dashboard-measure-v3.md"
BEGIN = "<!-- BEGIN GENERATED FROM signal_support.toml -->"
END = "<!-- END GENERATED FROM signal_support.toml -->"


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _code(value: object) -> str:
    return f"`{_cell(value)}`" if value else "—"


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


def _signals(registry: facts.Registry) -> list[str]:
    lines: list[str] = []
    views = dict.fromkeys(str(row["view_title"]) for row in registry.signals)
    for view in views:
        lines += [f"### {view}", ""]
        for signal in (row for row in registry.signals if row["view_title"] == view):
            lines += [
                f"#### {signal['title']} ({_code(signal['signal'])})",
                "",
                f"- **Question:** {signal['question']}",
                f"- **Unit:** {signal['unit']}",
                f"- **Formula:** {signal['formula']}",
                "",
            ]
            support = [row for row in registry.support if row["signal"] == signal["signal"]]
            if not support:
                lines += ["Scope: system (not per harness).", ""]
                continue
            lines += ["| Harness | Alignment | Route | Note |", "| --- | --- | --- | --- |"]
            lines += [
                f"| {row['harness_label']} | {row['alignment']} | {_code(row['route'])} | "
                f"{_cell(row['note']) or '—'} |"
                for row in support
            ]
            lines.append("")
    return lines


def render() -> str:
    """Return the Markdown catalog: routes, strays, then signals by view."""
    registry = facts.load_registry()
    labels = {str(row["harness"]): str(row["harness_label"]) for row in registry.routes}
    lines = [
        "## 3. Producer routes",
        "",
        "Each route names the rows of one or more harnesses in `introspection.spans` or",
        "`introspection.logs` that carry a signal. For an `events` route, no rows while",
        "the harness is otherwise active is a valid observation.",
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
    return "\n".join(lines).rstrip() + "\n"


def updated(text: str) -> str:
    """Replace the generated block in ``text`` with a fresh render."""
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{BEGIN}\n\n{render()}\n{END}{tail}"


def main(argv: list[str]) -> int:
    current = DOC.read_text()
    fresh = updated(current)
    if "--check" in argv:
        if fresh != current:
            print("docs/dashboard-measure-v3.md is out of date; run scripts/render_measures.py")
            return 1
        return 0
    DOC.write_text(fresh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
